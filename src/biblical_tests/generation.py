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


def _claim(fact: Fact, object_value: str | None = None) -> str:
    return f"{fact.evidence.reference}: {object_value or fact.object}"


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
    multi_pool = [fact for fact in pool if fact.id not in used]
    if len(multi_pool) < 3:
        raise GenerationError("Nu sunt suficiente facts distincte pentru o întrebare din Secțiunea IV.")
    for index in range(1, 4):
        # The same evidence card may appear in a different IV item when the
        # source selection is short, but never twice inside the same item.
        group = rng.sample(multi_pool, 3)
        correct_positions = set(rng.sample(range(3), rng.randint(0, 3)))
        options: dict[str, str] = {}
        correct: list[str] = []
        for position, fact in enumerate(group):
            letter = "ABC"[position]
            if position in correct_positions:
                options[letter] = _claim(fact)
                correct.append(letter)
            else:
                options[letter] = _claim(fact, _wrong_object(fact, facts))
        multis.append(MultiChoiceQuestion(
            f"IV-{index}", "Care dintre următoarele afirmații sunt susținute de pasajele selectate? (pot fi 0–3 răspunsuri)",
            options, correct, group[0].evidence, group[0].id,
            [fact.evidence for fact in group], [fact.id for fact in group],
        ))
    return TestDefinition(source, seed, version, contest, scoring, section_i=tf, section_ii=singles, section_iii=matching, section_iv=multis)
