from __future__ import annotations

import random
import re
from collections import defaultdict

from .models import Fact, MatchingQuestion, MultiChoiceQuestion, SingleChoiceQuestion, TestDefinition, TrueFalseQuestion


class GenerationError(ValueError):
    pass


def _balanced_letters(count: int, rng: random.Random) -> list[str]:
    letters = (["A", "B", "C"] * ((count + 2) // 3))[:count]
    rng.shuffle(letters)
    return letters


def _round_robin(facts: list[Fact], count: int, rng: random.Random) -> list[Fact]:
    by_chapter: dict[tuple[str, int], list[Fact]] = defaultdict(list)
    for fact in facts:
        by_chapter[(fact.evidence.book, fact.evidence.chapter)].append(fact)
    for group in by_chapter.values():
        rng.shuffle(group)
    keys = list(by_chapter)
    rng.shuffle(keys)
    selected = []
    while len(selected) < count:
        progressed = False
        for key in keys:
            if by_chapter[key] and len(selected) < count:
                selected.append(by_chapter[key].pop())
                progressed = True
        if not progressed:
            break
    return selected


def _wrong_object(fact: Fact, pool: list[Fact]) -> str:
    for option in fact.options:
        if option != fact.object:
            return option
    for candidate in pool:
        if candidate.id != fact.id and candidate.object != fact.object:
            return candidate.object
    raise GenerationError("Nu există suficiente fapte distincte pentru un distractor sigur.")


def _blanked_statement(fact: Fact, limit: int | None = None) -> str:
    before, marker, after = fact.statement.rpartition(fact.object)
    if not marker:
        raise GenerationError(f"Fact-ul {fact.id} nu conține obiectul în enunț.")
    result = before + "_____" + after
    if limit and len(result) > limit:
        target_position = result.find("_____")
        start = max(0, target_position - 46)
        end = min(len(result), target_position + 46)
        return ("…" if start else "") + result[start:end].strip() + ("…" if end < len(result) else "")
    return result


# Section IV follows the reference documents: a stem that breaks off at a colon
# and three short, parallel completions. One, two or three of them may be right.
_STEM_MIN_CHARS = 25
_STEM_MAX_CHARS = 150
# Only terms coordinated with the answer ("X și Y", "X, Y și Z") are also correct.
# Presence anywhere after the cut is not enough: in "fiul lui Saul, și doisprezece
# din slujitorii lui David", David follows Saul but does not complete "fiul lui:".
_COORDINATION = re.compile(r"^\s*(?:,\s*(?:și\s+|sau\s+)?|\s+(?:și|sau)\s+)")
_TERM = re.compile(r"[A-ZĂÂÎȘȚ][\wăâîșț-]*")


def _mentions(text: str, value: str) -> bool:
    return bool(re.search(rf"(?<!\w){re.escape(value)}(?!\w)", text))


def _coordinated_answers(answer_segment: str, answer: str) -> list[str]:
    """Reads the enumeration the verse opens with, so „Hofni și Fineas" yields both."""
    answers, rest = [answer], answer_segment[len(answer):]
    while True:
        link = _COORDINATION.match(rest)
        if not link:
            break
        rest = rest[link.end():]
        term = _TERM.match(rest)
        if not term:
            break
        answers.append(term.group(0))
        rest = rest[term.end():]
    return answers


def _completion_stem(fact: Fact) -> tuple[str, list[str], str] | None:
    """Splits a verse into a stem ending in ':' and the answers that complete it."""
    # Whole-word match only: "Domnul" must not be cut out of "Domnului",
    # which would leave the stem with no correct completion at all.
    hits = list(re.finditer(rf"(?<!\w){re.escape(fact.object)}(?!\w)", fact.statement))
    if not hits:
        return None
    last = hits[-1]
    prefix, rest = fact.statement[:last.start()], fact.statement[last.end():]
    # Keep only the closing sentence so the stem reads as a single clause.
    sentences = list(re.finditer(r"[.!?]\s+", prefix))
    if sentences:
        prefix = prefix[sentences[-1].end():]
    stem = prefix.strip().rstrip(" ,;:-–—„”\"'")
    if len(stem) < _STEM_MIN_CHARS:
        return None
    if len(stem) > _STEM_MAX_CHARS:
        tail = stem[-_STEM_MAX_CHARS:]
        space = tail.find(" ")
        stem = "…" + (tail[space + 1:] if space != -1 else tail)
    segment = fact.object + rest
    return stem + ":", _coordinated_answers(segment, fact.object), segment


def _completion_options(fact: Fact, facts: list[Fact], answers: list[str], segment: str, rng: random.Random) -> list[str] | None:
    """Builds three same-kind completions: the verse answers plus plausible distractors."""
    correct = answers[:3]
    # A distractor must be absent from the answer segment altogether. A term that
    # appears there without being provably coordinated is unsafe either way, so it
    # is never offered rather than being marked wrong on the key.
    # Inflected forms of the answer ("Domnul" against "Domnului") are not real
    # choices, so distractors must not share a stem with any correct answer.
    def variant(value: str) -> bool:
        for answer in answers:
            low, other = value.lower(), answer.lower()
            short, long = sorted((low, other), key=len)
            if len(short) >= 4 and long.startswith(short):
                return True
        return False

    usable = lambda value: value not in answers and not variant(value) and not _mentions(segment, value)
    distractors: list[str] = [value for value in fact.options if usable(value)]
    for candidate in facts:
        if len(distractors) >= 3 - len(correct):
            break
        if usable(candidate.object) and candidate.object not in distractors:
            distractors.append(candidate.object)
    values = [*correct, *distractors[:3 - len(correct)]]
    if len(set(values)) != 3:
        return None
    rng.shuffle(values)
    return values


def build_test(facts: list[Fact], source: dict[str, list[int]], contest: dict, scoring: dict[str, int], seed: int, version: int) -> TestDefinition:
    facts = [fact for fact in facts if fact.quality]
    if len(facts) < 20:
        raise GenerationError("Corpusul selectat necesită cel puțin 20 de facts verificate pentru un test complet.")
    rng = random.Random(seed + version - 1)
    pool = _round_robin(facts, len(facts), rng)
    used: set[str] = set()
    def take(count: int) -> list[Fact]:
        choices = [f for f in pool if f.id not in used]
        if len(choices) < count:
            raise GenerationError("Nu sunt suficiente facts distincte pentru a evita duplicarea între secțiuni.")
        result = choices[:count]
        used.update(f.id for f in result)
        return result

    safe_false = [fact for fact in pool if fact.object in {"Ana", "Penina", "Elcana", "Eli", "Hofni", "Fineas", "Samuel", "Silo", "Rama", "Israel"}]
    if len(safe_false) >= 5:
        false_facts = safe_false[:5]
        used.update(fact.id for fact in false_facts)
        true_facts = take(5)
        tf_facts = [item for pair in zip(true_facts, false_facts) for item in pair]
    else:
        tf_facts = take(10)
    tf: list[TrueFalseQuestion] = []
    for index, fact in enumerate(tf_facts, 1):
        is_true = index % 2 == 1
        statement = fact.statement if is_true else fact.statement.rpartition(fact.object)[0] + _wrong_object(fact, facts) + fact.statement.rpartition(fact.object)[2]
        tf.append(TrueFalseQuestion(f"I-{index}", statement, "A" if is_true else "F", fact.evidence, fact.id))

    single_facts = take(10)
    singles: list[SingleChoiceQuestion] = []
    for index, (fact, letter) in enumerate(zip(single_facts, _balanced_letters(10, rng)), 1):
        if len(fact.options) >= 3 and fact.object in fact.options:
            values = list(fact.options[:3])
        else:
            distractors = [_wrong_object(fact, [f for f in facts if f.id != fact.id])]
            distractors.append(next(f.object for f in facts if f.object not in {fact.object, distractors[0]}))
            values = [fact.object, *distractors]
        rng.shuffle(values)
        values.remove(fact.object)
        values.insert("ABC".index(letter), fact.object)
        singles.append(SingleChoiceQuestion(f"II-{index}", f"Completați corect enunțul: „{_blanked_statement(fact)}”", dict(zip("ABC", values)), letter, fact.evidence, fact.id))

    match_facts = []
    used_objects = set()
    for fact in (item for item in pool if item.id not in used):
        if fact.object not in used_objects:
            match_facts.append(fact)
            used_objects.add(fact.object)
            used.add(fact.id)
        if len(match_facts) == 5:
            break
    if len(match_facts) != 5:
        raise GenerationError("Nu sunt suficiente răspunsuri distincte pentru Secțiunea III.")
    right_facts = list(match_facts)
    rng.shuffle(right_facts)
    right = {letter: _blanked_statement(fact) for letter, fact in zip("ABCDE", right_facts)}
    answers = {str(i): next(letter for letter, fact_for_letter in zip("ABCDE", right_facts) if fact_for_letter.id == fact.id) for i, fact in enumerate(match_facts, 1)}
    matching = MatchingQuestion("III-1", [fact.object for fact in match_facts], right, answers, [f.evidence for f in match_facts], [f.id for f in match_facts])

    multis: list[MultiChoiceQuestion] = []
    candidates = []
    for fact in (item for item in pool if item.id not in used):
        built = _completion_stem(fact)
        if built:
            candidates.append((fact, *built))
    if len(candidates) < 3:
        raise GenerationError("Nu sunt suficiente versete potrivite pentru Secțiunea IV.")
    # Shorter stems read closer to the reference documents.
    readable = sorted(candidates, key=lambda item: len(item[1]))[:12]
    rng.shuffle(readable)
    index = 1
    for fact, stem, answers, segment in readable:
        if index > 3:
            break
        values = _completion_options(fact, facts, answers, segment, rng)
        if values is None:
            continue
        options = dict(zip("ABC", values))
        correct = [letter for letter, value in options.items() if value in answers]
        # The verse answer must survive as a correct option, or the key is wrong.
        if not any(options[letter] == fact.object for letter in correct):
            continue
        multis.append(MultiChoiceQuestion(f"IV-{index}", stem, options, correct, fact.evidence, fact.id, [fact.evidence], [fact.id]))
        used.add(fact.id)
        index += 1
    if len(multis) != 3:
        raise GenerationError("Nu s-au putut construi trei întrebări verificabile pentru Secțiunea IV.")
    return TestDefinition(source, seed, version, contest, scoring, section_i=tf, section_ii=singles, section_iii=matching, section_iv=multis)
