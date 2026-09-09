from __future__ import annotations

import re
from collections import Counter
from dataclasses import replace

from .generation import GenerationError, _MAX_SAME_ANSWER_RELAXED, _concise, _enumeration, _same_referent, _safe_to_swap, _sentences, _wrong_object
from .models import Evidence, Fact, SingleChoiceQuestion, TestDefinition, TrueFalseQuestion
from .repository import BibleRepository


class ValidationError(ValueError):
    pass


def _in_scope(evidence: Evidence, selected: dict[str, list[int]]) -> bool:
    return evidence.book in selected and evidence.chapter in selected[evidence.book]


def _validate_true_false(question: TrueFalseQuestion, fact: Fact, targets: set[str]) -> None:
    # Reconstruct the source from the cited text, never from an independently
    # supplied JSON statement. Equality alone cannot establish a false answer:
    # it must be precisely one permitted entity substitution in that source.
    authentic = replace(fact, statement=question.evidence.text)
    if question.evidence != fact.evidence:
        raise ValidationError(f"Referința nu corespunde faptei: {question.id}")
    if question.answer == "A":
        if question.statement in _sentences(authentic.statement):
            return
    elif question.answer == "F":
        source = _concise(authentic, True)
        if source and _safe_to_swap(source, fact.object):
            hits = list(re.finditer(rf"(?<!\w){re.escape(fact.object)}(?!\w)", source))
            if hits:
                hit = hits[-1]
                before, after = source[:hit.start()], source[hit.end():]
                statement = question.statement
                if statement.startswith(before) and statement.endswith(after):
                    end = len(statement) - len(after) if after else len(statement)
                    replacement = statement[len(before):end]
                    candidate = replace(authentic, id=authentic.id + "-replacement", object=replacement)
                    try:
                        accepted = _wrong_object(replace(authentic, options=()), [candidate], lead=before, statement=f"{before} {after}")
                    except GenerationError:
                        accepted = None
                    if replacement in targets and accepted == replacement and statement == before + replacement + after:
                        return
    raise ValidationError(f"Baremul A/F nu este susținut de dovadă: {question.id}")


def validate_test(test: TestDefinition) -> None:
    errors: list[str] = []
    if len(test.section_i) != 10:
        errors.append("Secțiunea I trebuie să conțină 10 întrebări.")
    if len(test.section_ii) != 10:
        errors.append("Secțiunea II trebuie să conțină 10 întrebări.")
    if test.section_iii is None or len(test.section_iii.left) != 5 or len(test.section_iii.right) != 5:
        errors.append("Secțiunea III trebuie să conțină 5 asocieri.")
    if len(test.section_iv) != 3:
        errors.append("Secțiunea IV trebuie să conțină 3 întrebări.")
    for question in test.section_i:
        if question.answer not in {"A", "F"} or not question.statement or not _in_scope(question.evidence, test.source):
            errors.append(f"Item I invalid: {question.id}")
    single_answers = []
    for question in test.section_ii:
        single_answers.append(question.correct)
        if set(question.options) != {"A", "B", "C"} or question.correct not in question.options or len(set(question.options.values())) != 3 or not _in_scope(question.evidence, test.source):
            errors.append(f"Item II invalid: {question.id}")
    if single_answers and max(Counter(single_answers).values()) > 4:
        errors.append("Distribuția răspunsurilor din II nu este echilibrată.")
    # The check above balances the *letters*; this one balances what those
    # letters say. They are not the same property, and only the second one
    # stops a page whose ten answers are „David" nine times over — perfectly
    # spread across A/B/C and still answerable without reading a word.
    single_texts = [question.options[question.correct] for question in test.section_ii]
    if single_texts and max(Counter(single_texts).values()) > _MAX_SAME_ANSWER_RELAXED:
        errors.append("Un singur răspuns se repetă prea des în Secțiunea II.")
    # Three options have to name three different answers, not merely be three
    # different strings: „Domnul" beside „Dumnezeul" is one answer written
    # twice, which either duplicates the correct one or hands the student two
    # options to eliminate at once.
    for question in test.section_ii + test.section_iv:
        values = list(question.options.values())
        if any(_same_referent(one, other) for index, one in enumerate(values) for other in values[index + 1:]):
            errors.append(f"Două variante desemnează același răspuns: {question.id}")
    if test.section_iii:
        match = test.section_iii
        if len(match.evidence) != 5 or len(match.fact_ids) != 5:
            errors.append("Secțiunea III trebuie să aibă cinci dovezi și cinci fapte.")
        if set(match.right) != set("ABCDE") or set(match.answers) != set("12345") or set(match.answers.values()) != set("ABCDE") or len(set(match.right.values())) != 5:
            errors.append("Asocierile din III nu formează o bijecție.")
        if not all(_in_scope(item, test.source) for item in match.evidence):
            errors.append("O referință din III nu este în selecție.")
    for question in test.section_iv:
        evidence = question.supporting_evidence or [question.evidence]
        if set(question.options) != {"A", "B", "C"} or not set(question.correct).issubset({"A", "B", "C"}) or len(set(question.options.values())) != 3 or not all(_in_scope(item, test.source) for item in evidence):
            errors.append(f"Item IV invalid: {question.id}")
    core_ids = [q.fact_id for q in test.section_i + test.section_ii]
    if test.section_iii:
        core_ids.extend(test.section_iii.fact_ids)
    iv_fact_sets = [set(question.fact_ids or [question.fact_id]) for question in test.section_iv]
    all_fact_ids = core_ids + [fact_id for fact_ids in iv_fact_sets for fact_id in fact_ids]
    if len(all_fact_ids) != len(set(all_fact_ids)) or any(not 1 <= len(question.fact_ids or [question.fact_id]) <= 3 for question in test.section_iv):
        errors.append("Aceeași factă este reutilizată între întrebări.")
    if errors:
        raise ValidationError("\n".join(errors))


def coverage_report(test: TestDefinition) -> dict[str, int]:
    refs = [q.evidence for q in test.section_i + test.section_ii]
    refs.extend(ref for q in test.section_iv for ref in (q.supporting_evidence or [q.evidence]))
    if test.section_iii:
        refs.extend(test.section_iii.evidence)
    return dict(Counter(f"{ref.book} {ref.chapter}" for ref in refs))


def validate_evidence(test: TestDefinition, repository: BibleRepository) -> None:
    """Confirms every stored evidence excerpt is exactly the local corpus text."""
    evidence = [q.evidence for q in test.section_i + test.section_ii]
    evidence.extend(ref for q in test.section_iv for ref in (q.supporting_evidence or [q.evidence]))
    if test.section_iii:
        evidence.extend(test.section_iii.evidence)
    facts_by_id = {fact.id: fact for fact in repository.facts}
    targets = {fact.object for fact in repository.facts if _in_scope(fact.evidence, test.source)}
    for question in test.section_i + test.section_ii:
        fact = facts_by_id.get(question.fact_id)
        if fact is None:
            raise ValidationError(f"Faptă necunoscută: {question.fact_id}")
        if isinstance(question, TrueFalseQuestion):
            _validate_true_false(question, fact, targets)
        # The correct option must be a word the cited verse actually contains.
        # This used to demand it equal `fact.object`, which was the same thing
        # while every Section II shape answered with the fact's own object — the
        # place and numeral shapes answer with a place the verse names or the
        # quantity it states, and „`fact.object`" would reject them while saying
        # nothing extra about the ones it accepted. Grounding the answer in the
        # evidence text is the property a barem actually needs, and it holds for
        # the object shapes too, since the object is drawn from that same text.
        if isinstance(question, SingleChoiceQuestion):
            answer = question.options[question.correct]
            if not re.search(rf"(?<!\w){re.escape(answer)}(?!\w)", question.evidence.text):
                raise ValidationError(f"Răspunsul corect nu apare în versetul citat: {question.id}")
    for question in test.section_iv:
        supporting = question.supporting_evidence or [question.evidence]
        text = " ".join(ref.text for ref in supporting)
        # An item nobody can answer correctly is not a harder item, it is a
        # broken one, and every check below this was written to inspect the
        # letters in `correct` — so an empty list satisfied all of them by
        # having nothing to inspect.
        if not question.correct:
            raise ValidationError(f"Întrebarea nu are niciun răspuns corect: {question.id}")
        for letter in question.correct:
            if question.options[letter] not in text:
                raise ValidationError(f"Răspunsul corect nu este susținut de dovadă: {question.id}")
        # The other direction: a *missing* correct answer. Checking only the
        # marked letters can never see one, so the barem could credit two of
        # the three vessels Ioram brought and silently mark the third wrong.
        #
        # The test is not "does this option appear in the verse" — a plausible
        # distractor often does, and legitimately so ("Domnul" named elsewhere
        # in a verse whose answer is "Samuel"). It is the enumeration itself:
        # when the item was built from a coordinated list, that list says
        # exactly which options belong to it, and every one of them has to be
        # credited. Items of any other shape have no such list and are left to
        # the support check above.
        # Only for an item that *is* the enumeration shape. A fact carrying a
        # coordinated list can just as easily be the source of a wh-question
        # built on something else in the same verse, and that item's options
        # have no reason to be members of the list. Its own stem is what says
        # which: the enumeration shape asks with exactly the stem
        # `_enumeration` derives, so comparing them tells the two apart
        # without guessing from the answer count.
        fact = facts_by_id.get(question.fact_id)
        listed = _enumeration(fact) if fact else None
        if listed and question.question == f"{listed[0]}":
            members = set(listed[1])
            belong = {letter for letter, value in question.options.items() if value in members}
            if belong != set(question.correct):
                raise ValidationError(
                    f"Baremul nu acoperă toate răspunsurile din enumerare: {question.id} "
                    f"(în dovadă: {sorted(belong)}; în barem: {sorted(question.correct)})"
                )
    if test.section_iii:
        for index, ref in enumerate(test.section_iii.evidence):
            if index >= len(test.section_iii.left):
                break
            letter = test.section_iii.answers.get(str(index + 1))
            if not letter or test.section_iii.left[index] not in ref.text or test.section_iii.right.get(letter, "") not in ref.text:
                raise ValidationError(f"Asocierea nu este susținută de dovadă: III-{index + 1}")
    for item in evidence:
        expected = " ".join(repository.get_verse(item.book, item.chapter, verse) for verse in range(item.verse_start, item.verse_end + 1))
        if " ".join(item.text.split()) != " ".join(expected.split()):
            raise ValidationError(f"Dovada nu coincide cu corpusul local: {item.reference}")
