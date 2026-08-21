from __future__ import annotations

import random
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
    right_values = [fact.object for fact in match_facts]
    rng.shuffle(right_values)
    right = dict(zip("ABCDE", right_values))
    answers = {str(i): next(letter for letter, value in right.items() if value == fact.object) for i, fact in enumerate(match_facts, 1)}
    matching = MatchingQuestion("III-1", [_blanked_statement(fact, limit=80) for fact in match_facts], right, answers, [f.evidence for f in match_facts], [f.id for f in match_facts])

    multi_facts = take(3)
    multis: list[MultiChoiceQuestion] = []
    # The builder supports any 0..3-correct configuration; the default mix uses
    # one answer so a corpus containing independent evidence cards is sufficient.
    patterns = [["A"], ["B"], ["C"]]
    for index, (fact, correct) in enumerate(zip(multi_facts, patterns), 1):
        candidates = [value for value in [*fact.options, *(f.object for f in facts)] if value != fact.object]
        wrong_values = []
        for value in candidates:
            if value not in wrong_values:
                wrong_values.append(value)
            if len(wrong_values) == 2:
                break
        if len(wrong_values) < 2:
            raise GenerationError("Nu există distractori distincți pentru Secțiunea IV.")
        options = {"A": wrong_values[0], "B": wrong_values[1], "C": _wrong_object(fact, list(reversed(facts)))}
        if len(set(options.values())) != 3:
            options["C"] = next(f.object for f in facts if f.object not in set(options.values()) and f.object != fact.object)
        options[correct[0]] = fact.object
        multis.append(MultiChoiceQuestion(f"IV-{index}", f"Care dintre următoarele variante este susținută de text? ({fact.subject} {fact.predicate})", options, correct, fact.evidence, fact.id))
    return TestDefinition(source, seed, version, contest, scoring, section_i=tf, section_ii=singles, section_iii=matching, section_iv=multis)
