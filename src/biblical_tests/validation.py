from __future__ import annotations

from collections import Counter

from .models import Evidence, TestDefinition
from .repository import BibleRepository


class ValidationError(ValueError):
    pass


def _in_scope(evidence: Evidence, selected: dict[str, list[int]]) -> bool:
    return evidence.book in selected and evidence.chapter in selected[evidence.book]


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
    if test.section_iii:
        match = test.section_iii
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
    if len(core_ids) != len(set(core_ids)) or any(len(question.fact_ids or [question.fact_id]) != 3 for question in test.section_iv) or any(set(core_ids) & fact_ids for fact_ids in iv_fact_sets):
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
    for item in evidence:
        expected = " ".join(repository.get_verse(item.book, item.chapter, verse) for verse in range(item.verse_start, item.verse_end + 1))
        if " ".join(item.text.split()) != " ".join(expected.split()):
            raise ValidationError(f"Dovada nu coincide cu corpusul local: {item.reference}")
