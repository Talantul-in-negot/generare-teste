from __future__ import annotations

import random
import re
from collections import defaultdict

from .models import Fact, MatchingQuestion, MultiChoiceQuestion, SingleChoiceQuestion, TestDefinition, TrueFalseQuestion


class GenerationError(ValueError):
    pass


# Shapes taken from the reference answer keys in data/:
#   I   short affirmations, one clause long;
#   II  a stem that breaks off at a colon, three short completions, one correct;
#   III two halves of the same short clause, matched across five rows;
#   IV  a stem plus parallel phrases, of which one, two or three may be correct.
_SENTENCES = re.compile(r"(?<=[.!?])\s+")
_CONJUNCTION = re.compile(r"\s+(?:și|sau)\s+")
_TRIM = " ,;:-–—„”\"'!?."
_DEITY = {"Domnul", "Domnului", "Dumnezeu", "Dumnezeul"}
_STEM_MIN_CHARS = 25
_STEM_MAX_CHARS = 150


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCES.split(text) if part.strip()]


def _mentions(text: str, value: str) -> bool:
    return bool(re.search(rf"(?<!\w){re.escape(value)}(?!\w)", text))


def _inflection(value: str, answers: list[str]) -> bool:
    """True for forms like „Domnul" against „Domnului", which are not real choices."""
    for answer in answers:
        short, long = sorted((value.lower(), answer.lower()), key=len)
        if len(short) >= 4 and long.startswith(short):
            return True
    return False


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
    # Only names the selected chapters actually use, so a 2 Samuel test never
    # swaps in a character who appears nowhere in it.
    inside = {candidate.object for candidate in pool}
    for option in fact.options:
        if option != fact.object and option in inside:
            return option
    for candidate in pool:
        if candidate.id != fact.id and candidate.object != fact.object:
            return candidate.object
    raise GenerationError("Nu există suficiente fapte distincte pentru un distractor sigur.")


def _concise(fact: Fact, need_object: bool) -> str | None:
    """Section I keeps one clean clause; a verse that offers none is skipped."""
    for sentence in sorted(_sentences(fact.statement), key=len):
        if not 30 <= len(sentence) <= 165 or not sentence[:1].isupper():
            continue
        if sentence.count('"') % 2 or "(" in sentence or "«" in sentence:
            continue
        if need_object and not _mentions(sentence, fact.object):
            continue
        return sentence
    return None


def _completion_stem(fact: Fact) -> tuple[str, str] | None:
    """Splits a verse into a stem ending in ':' and the segment that answers it."""
    # Whole-word match only: "Domnul" must not be cut out of "Domnului",
    # which would leave the stem with no correct completion at all.
    hits = list(re.finditer(rf"(?<!\w){re.escape(fact.object)}(?!\w)", fact.statement))
    if not hits:
        return None
    last = hits[-1]
    prefix, rest = fact.statement[:last.start()], fact.statement[last.end():]
    sentences = list(re.finditer(r"[.!?]\s+", prefix))
    if sentences:
        prefix = prefix[sentences[-1].end():]
    stem = prefix.strip().rstrip(_TRIM)
    if not _STEM_MIN_CHARS <= len(stem) <= _STEM_MAX_CHARS:
        return None
    return stem + ":", fact.object + rest


def _name_predicate(fact: Fact) -> str | None:
    """Section III pairs a name with what the verse says about it, as barem 2_3 does."""
    for sentence in _sentences(fact.statement):
        hit = re.search(rf"(?<!\w){re.escape(fact.object)}(?!\w)", sentence)
        if not hit:
            continue
        predicate = sentence[hit.end():].strip(_TRIM)
        words = predicate.split()
        if not 2 <= len(words) <= 9:
            continue
        # A clean predicate carries no internal punctuation of its own.
        if not _clean_member(predicate):
            continue
        # The name must not reappear, or the association gives itself away.
        if len(predicate) < 10 or _mentions(predicate, fact.object):
            continue
        return predicate
    return None


# A phrase opening with one of these is a dependent fragment ("ce plângi",
# "care veneau"): it only reads as an option beside other fragments of the
# same kind, never beside a full clause such as "El smerește".
_SUBORDINATE = {"ce", "cum", "cine", "unde", "când", "care", "cui", "dacă", "că", "să", "ca"}
_LINKERS = {"și", "sau", "dar", "iar"}


def _clean_member(value: str) -> bool:
    if any(mark in value for mark in ('.', ',', ';', ':', '"', '„', '”', '«', '»', '!', '?')):
        return False
    first_word = value.split()[0].lower() if value.split() else ""
    return first_word not in _LINKERS


def _register(value: str) -> str:
    """Groups options by the shape of their opening word, so they stay parallel."""
    words = value.split()
    if not words:
        return "empty"
    head = words[0]
    if head.lower() in _SUBORDINATE:
        return "subordinate"
    if head[:1].isupper():
        return "capitalised"
    return "plain"


def _enumeration(fact: Fact) -> tuple[str, list[str]] | None:
    """Finds the coordinated list behind the reference's multi-answer items."""
    for sentence in _sentences(fact.statement):
        links = list(_CONJUNCTION.finditer(sentence))
        if not links:
            continue
        link = links[-1]
        tail = sentence[link.end():].strip(_TRIM)
        head_words = sentence[:link.start()].split()
        size = len(tail.split())
        if not _clean_member(tail) or not 2 <= size <= 8 or len(head_words) < size + 4:
            continue
        mid = " ".join(head_words[-size:]).strip(_TRIM)
        if not _clean_member(mid) or not 2 <= len(mid.split()) <= 8:
            continue
        members = [mid, tail]
        head = " ".join(head_words[:-size])
        # "a, b si c": a comma right before the second member marks a third one.
        if head.rstrip().endswith(","):
            earlier = head.rstrip().rstrip(",").split()
            if len(earlier) >= size + 4:
                first = " ".join(earlier[-size:]).strip(_TRIM)
                if _clean_member(first) and 2 <= len(first.split()) <= 8 and len(first) >= 6:
                    members.insert(0, first)
                    head = " ".join(earlier[:-size])
        stem = head.strip(_TRIM)
        if not _STEM_MIN_CHARS <= len(stem) <= _STEM_MAX_CHARS:
            continue
        if len(mid) < 6 or len(tail) < 6:
            continue
        if len({member.lower() for member in members}) != len(members):
            continue
        return stem + ":", members
    return None


def _section_iv(pool: list[Fact], facts: list[Fact], used: set[str], rng: random.Random) -> list[MultiChoiceQuestion]:
    """Mirrors the reference mix: items with three, two and one correct answer."""
    candidates = [(fact, *found) for fact in pool if (found := _enumeration(fact))]
    foreign = [member for _, _, members in candidates for member in members]
    multis: list[MultiChoiceQuestion] = []

    def add(fact: Fact, stem: str, values: list[str], correct_values: list[str]) -> bool:
        if len({value.lower() for value in values}) != 3:
            return False
        rng.shuffle(values)
        options = dict(zip("ABC", values))
        correct = [letter for letter, value in options.items() if value in correct_values]
        if len(correct) != len(correct_values):
            return False
        multis.append(MultiChoiceQuestion(f"IV-{len(multis) + 1}", stem, options, correct, fact.evidence, fact.id, [fact.evidence], [fact.id]))
        used.add(fact.id)
        return True

    # Three, then two, then one correct answer, matching how the reference varies.
    for wanted in (3, 2, 1):
        for fact, stem, members in candidates:
            if fact.id in used or len(members) < wanted:
                continue
            correct_values = members[:wanted]
            # A distractor has to match the correct answers in register as well as
            # in length, or grammar alone gives it away.
            registers = {_register(value) for value in correct_values}
            outside = [
                value for value in foreign
                if not _mentions(fact.statement, value) and value not in members
                and _register(value) in registers
            ]
            target = sum(len(value.split()) for value in correct_values) / len(correct_values)
            outside.sort(key=lambda value: abs(len(value.split()) - target))
            picks = []
            for value in outside:
                if len(picks) == 3 - wanted:
                    break
                if value.lower() not in {item.lower() for item in picks}:
                    picks.append(value)
            if len(picks) != 3 - wanted:
                continue
            if add(fact, stem, [*correct_values, *picks], correct_values):
                break
    # A verse that merely names a person still makes a sound single-answer item.
    for fact in pool:
        if len(multis) == 3:
            break
        if fact.id in used or not (built := _completion_stem(fact)):
            continue
        stem, segment = built
        distractors = [f.object for f in facts if f.object != fact.object and not _mentions(segment, f.object) and not _inflection(f.object, [fact.object])]
        if len(distractors) < 2:
            continue
        add(fact, stem, [fact.object, *distractors[:2]], [fact.object])
    if len(multis) != 3:
        raise GenerationError("Nu s-au putut construi trei intrebari verificabile pentru Sectiunea IV.")
    return multis


def _section_iii(pool: list[Fact], used: set[str], rng: random.Random) -> MatchingQuestion:
    rows: list[tuple[Fact, str, str]] = []
    seen: list[str] = []
    # Named characters read best; divine forms are a fallback because "Domnul",
    # "Domnului" and "Dumnezeu" would otherwise fill the column with one name.
    for allow_deity in (False, True):
        for fact in pool:
            if len(rows) == 5:
                break
            if fact.id in used or fact.object in seen or not (predicate := _name_predicate(fact)):
                continue
            if not allow_deity and fact.object in _DEITY:
                continue
            if _inflection(fact.object, seen) or any(predicate == other[2] for other in rows):
                continue
            rows.append((fact, fact.object, predicate))
            seen.append(fact.object)
            used.add(fact.id)
    if len(rows) != 5:
        raise GenerationError("Nu sunt suficiente asocieri distincte pentru Sectiunea III.")
    shuffled = list(rows)
    rng.shuffle(shuffled)
    right_column = {letter: item[2] for letter, item in zip("ABCDE", shuffled)}
    answers = {
        str(index): next(letter for letter, item in zip("ABCDE", shuffled) if item[0].id == fact.id)
        for index, (fact, _, _) in enumerate(rows, 1)
    }
    return MatchingQuestion(
        "III-1", [name for _, name, _ in rows], right_column, answers,
        [fact.evidence for fact, _, _ in rows], [fact.id for fact, _, _ in rows],
    )


def _section_ii(pool: list[Fact], facts: list[Fact], used: set[str], rng: random.Random) -> list[SingleChoiceQuestion]:
    singles: list[SingleChoiceQuestion] = []
    letters = _balanced_letters(10, rng)
    for fact in pool:
        if len(singles) == 10:
            break
        if fact.id in used or not (built := _completion_stem(fact)):
            continue
        stem, segment = built
        # A distractor present in the answer segment could also complete the stem,
        # so only terms the verse does not offer at all are safe to mark wrong.
        safe = lambda value: value != fact.object and not _mentions(segment, value) and not _inflection(value, [fact.object])
        choices = [value for value in fact.options if safe(value)]
        choices += [f.object for f in facts if safe(f.object) and f.object not in choices]
        values = [fact.object, *choices[:2]]
        if len(set(values)) != 3:
            continue
        letter = letters[len(singles)]
        rng.shuffle(values)
        values.remove(fact.object)
        values.insert("ABC".index(letter), fact.object)
        singles.append(SingleChoiceQuestion(f"II-{len(singles) + 1}", stem, dict(zip("ABC", values)), letter, fact.evidence, fact.id))
        used.add(fact.id)
    if len(singles) != 10:
        raise GenerationError("Nu sunt suficiente versete potrivite pentru Secțiunea II.")
    return singles


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

    # The sections are built from the most constrained verse shape to the least:
    # enumerations are scarce, split clauses less so, and a plain affirmation
    # can be made from almost any verse.
    multis = _section_iv(pool, facts, used, rng)
    matching = _section_iii(pool, used, rng)
    singles = _section_ii(pool, facts, used, rng)

    false_pool = [fact for fact in pool if fact.id not in used and _concise(fact, True)]
    true_pool = [fact for fact in pool if fact.id not in used and _concise(fact, False)]
    tf: list[TrueFalseQuestion] = []
    for index in range(1, 11):
        is_true = index % 2 == 1
        source_pool = true_pool if is_true else false_pool
        fact = next((item for item in source_pool if item.id not in used), None)
        if fact is None:
            fact = next((item for item in false_pool + true_pool if item.id not in used), None)
        if fact is None:
            raise GenerationError("Nu sunt suficiente versete potrivite pentru Sectiunea I.")
        used.add(fact.id)
        statement = _concise(fact, not is_true) or _concise(fact, False)
        if not is_true:
            before, _, after = statement.rpartition(fact.object)
            statement = before + _wrong_object(fact, facts) + after
        tf.append(TrueFalseQuestion(f"I-{index}", statement, "A" if is_true else "F", fact.evidence, fact.id))

    return TestDefinition(source, seed, version, contest, scoring, section_i=tf, section_ii=singles, section_iii=matching, section_iv=multis)
