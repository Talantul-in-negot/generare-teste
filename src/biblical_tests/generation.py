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
    for candidate in pool:
        if candidate.id != fact.id and candidate.object != fact.object:
            return candidate.object
    raise GenerationError("Nu există suficiente fapte distincte pentru un distractor sigur.")


def build_test(facts: list[Fact], source: dict[str, list[int]], contest: dict, scoring: dict[str, int], seed: int, version: int) -> TestDefinition:
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

    tf_facts = take(10)
    tf: list[TrueFalseQuestion] = []
    for index, fact in enumerate(tf_facts, 1):
        is_true = index % 2 == 1
        if is_true:
            statement = fact.statement
        else:
            before, marker, after = fact.statement.rpartition(fact.object)
            if not marker:
                raise GenerationError(f"Fact-ul {fact.id} nu conține obiectul în enunț.")
            statement = before + _wrong_object(fact, facts) + after
        tf.append(TrueFalseQuestion(f"I-{index}", statement, "A" if is_true else "F", fact.evidence, fact.id))

    single_facts = take(10)
    singles: list[SingleChoiceQuestion] = []
    for index, (fact, letter) in enumerate(zip(single_facts, _balanced_letters(10, rng)), 1):
        distractors = [_wrong_object(fact, [f for f in facts if f.id != fact.id])]
        distractors.append(next(f.object for f in facts if f.object not in {fact.object, distractors[0]}))
        values = [fact.object, *distractors]
        rng.shuffle(values)
        values.remove(fact.object)
        values.insert("ABC".index(letter), fact.object)
        singles.append(SingleChoiceQuestion(f"II-{index}", f"Ce anume {fact.predicate} {fact.subject}?", dict(zip("ABC", values)), letter, fact.evidence, fact.id))

    match_facts = take(5)
    right_values = [fact.object for fact in match_facts]
    rng.shuffle(right_values)
    right = dict(zip("ABCDE", right_values))
    answers = {str(i): next(letter for letter, value in right.items() if value == fact.object) for i, fact in enumerate(match_facts, 1)}
    matching = MatchingQuestion("III-1", [f"{fact.subject} {fact.predicate}" for fact in match_facts], right, answers, [f.evidence for f in match_facts], [f.id for f in match_facts])

    multi_facts = take(3)
    multis: list[MultiChoiceQuestion] = []
    # The builder supports any 0..3-correct configuration; the default mix uses
    # one answer so a corpus containing independent evidence cards is sufficient.
    patterns = [["A"], ["B"], ["C"]]
    for index, (fact, correct) in enumerate(zip(multi_facts, patterns), 1):
        wrong1 = _wrong_object(fact, facts)
        wrong2 = next(f.object for f in facts if f.object not in {fact.object, wrong1})
        options = {"A": wrong1, "B": wrong2, "C": _wrong_object(fact, list(reversed(facts)))}
        options[correct[0]] = fact.object
        multis.append(MultiChoiceQuestion(f"IV-{index}", f"Care dintre următoarele variante este susținută de text? ({fact.subject} {fact.predicate})", options, correct, fact.evidence, fact.id))
    return TestDefinition(source, seed, version, contest, scoring, section_i=tf, section_ii=singles, section_iii=matching, section_iv=multis)
