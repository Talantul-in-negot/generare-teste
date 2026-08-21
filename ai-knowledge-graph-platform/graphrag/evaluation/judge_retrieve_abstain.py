"""Risk-controlled evaluation policy: judge, retrieve, or abstain.

The first judge uses only the answer, question, and (when available) the
trusted reference answer. Retrieval-backed metrics are deliberately deferred
until the calibrated policy says the cheap verdict is uncertain. This keeps
the policy auditable and avoids turning a missing/failed judge call into a
silently accepted score.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Iterable


class JudgeDecision(StrEnum):
    ACCEPT = "accept"
    RETRIEVE = "retrieve"
    ABSTAIN = "abstain"


@dataclass(frozen=True)
class CalibrationThresholds:
    """Operating point learned from labeled golden records."""

    accept_threshold: float = 0.90
    retrieve_threshold: float = 0.55
    target_fdr: float = 0.05

    def __post_init__(self) -> None:
        if not 0.0 <= self.retrieve_threshold <= self.accept_threshold <= 1.0:
            raise ValueError("thresholds must satisfy 0 <= retrieve <= accept <= 1")
        if not 0.0 <= self.target_fdr <= 1.0:
            raise ValueError("target_fdr must be between 0 and 1")


@dataclass(frozen=True)
class JudgeRetrieveAbstainResult:
    decision: JudgeDecision
    confidence: float
    thresholds: CalibrationThresholds
    retrieval_used: bool = False
    retrieved_score: float | None = None
    abstention_reason: str = ""

    def metrics(self, *, tenant: str, query_id: str, retrieval_cost_usd: float = 0.0) -> dict[str, Any]:
        """Return a BigQuery/KPI-friendly, scalar-only record."""
        return {
            "tenant": tenant,
            "query_id": query_id,
            "decision": self.decision.value,
            "confidence": self.confidence,
            "accept_threshold": self.thresholds.accept_threshold,
            "retrieve_threshold": self.thresholds.retrieve_threshold,
            "target_fdr": self.thresholds.target_fdr,
            "retrieval_used": self.retrieval_used,
            "retrieved_score": self.retrieved_score,
            "abstention_reason": self.abstention_reason,
            "retrieval_cost_usd": retrieval_cost_usd,
        }


_TOKEN_RE = re.compile(r"[\w]+(?:[-'][\w]+)*", re.UNICODE)


def _tokens(value: str) -> set[str]:
    return {token.casefold() for token in _TOKEN_RE.findall(value or "")}


def answer_overlap_confidence(answer: str, reference: str) -> float:
    """Compute a deterministic reference-answer confidence in ``[0, 1]``."""
    answer_tokens = _tokens(answer)
    reference_tokens = _tokens(reference)
    if not answer_tokens or not reference_tokens:
        return 0.0
    overlap = len(answer_tokens & reference_tokens)
    precision = overlap / len(answer_tokens)
    recall = overlap / len(reference_tokens)
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def calibrate_thresholds(
    records: Iterable[dict[str, Any]], *, target_fdr: float = 0.05,
) -> CalibrationThresholds:
    """Choose the highest-coverage accept threshold meeting the FDR target.

    Records must contain ``confidence`` and a boolean ``correct`` label. A
    record without a valid label is ignored; silently treating it as correct
    would invalidate the risk guarantee.
    """
    if not 0.0 <= target_fdr <= 1.0:
        raise ValueError("target_fdr must be between 0 and 1")
    labeled = [
        (max(0.0, min(1.0, float(row["confidence"]))), bool(row["correct"]))
        for row in records
        if isinstance(row.get("correct"), bool)
        and isinstance(row.get("confidence"), (int, float))
        and math.isfinite(float(row["confidence"]))
    ]
    if not labeled:
        return CalibrationThresholds(target_fdr=target_fdr)

    candidates = sorted({confidence for confidence, _ in labeled}, reverse=True)
    valid = []
    for threshold in candidates:
        accepted = [correct for confidence, correct in labeled if confidence >= threshold]
        if accepted:
            fdr = sum(not correct for correct in accepted) / len(accepted)
            if fdr <= target_fdr:
                valid.append((len(accepted), threshold))
    accept = max(valid, default=(0, 0.90))[1]
    # The retrieve band starts below the accept point but remains conservative:
    # very low confidence goes straight to abstention after the retrieval pass.
    retrieve = round(max(0.0, min(accept, accept * 0.60)), 4)
    return CalibrationThresholds(
        accept_threshold=round(accept, 4),
        retrieve_threshold=retrieve,
        target_fdr=target_fdr,
    )


def judge_without_retrieval(
    *, answer: str, reference: str = "", thresholds: CalibrationThresholds | None = None,
) -> JudgeRetrieveAbstainResult:
    """Make the cheap first decision without consulting retrieved context."""
    thresholds = thresholds or CalibrationThresholds()
    confidence = answer_overlap_confidence(answer, reference) if reference else 0.0
    if not answer.strip() or _looks_like_refusal(answer):
        return JudgeRetrieveAbstainResult(
            JudgeDecision.ABSTAIN, confidence, thresholds,
            abstention_reason="empty_or_refusal_answer",
        )
    if confidence >= thresholds.accept_threshold:
        return JudgeRetrieveAbstainResult(JudgeDecision.ACCEPT, confidence, thresholds)
    if confidence >= thresholds.retrieve_threshold:
        return JudgeRetrieveAbstainResult(
            JudgeDecision.RETRIEVE, confidence, thresholds, retrieval_used=True,
        )
    return JudgeRetrieveAbstainResult(
        JudgeDecision.RETRIEVE, confidence, thresholds, retrieval_used=True,
        abstention_reason="initial_confidence_below_accept_threshold",
    )


def finalize_after_retrieval(
    initial: JudgeRetrieveAbstainResult, retrieved_score: float | None,
    *, accept_threshold: float | None = None,
) -> JudgeRetrieveAbstainResult:
    """Convert a finite retrieval-backed score into accept or abstain."""
    thresholds = initial.thresholds
    if accept_threshold is not None:
        thresholds = replace(
            thresholds,
            accept_threshold=float(accept_threshold),
            retrieve_threshold=min(thresholds.retrieve_threshold, float(accept_threshold)),
        )
    score = float(retrieved_score) if retrieved_score is not None else math.nan
    if not math.isfinite(score):
        return JudgeRetrieveAbstainResult(
            JudgeDecision.ABSTAIN, initial.confidence, thresholds,
            retrieval_used=True, abstention_reason="retrieval_score_unavailable",
        )
    score = max(0.0, min(1.0, score))
    if score >= thresholds.accept_threshold:
        return JudgeRetrieveAbstainResult(
            JudgeDecision.ACCEPT, score, thresholds,
            retrieval_used=True, retrieved_score=score,
        )
    return JudgeRetrieveAbstainResult(
        JudgeDecision.ABSTAIN, score, thresholds,
        retrieval_used=True, retrieved_score=score,
        abstention_reason="retrieval_score_below_accept_threshold",
    )


def _looks_like_refusal(answer: str) -> bool:
    text = answer.casefold()
    return any(marker in text for marker in (
        "insufficient context", "cannot answer", "not enough information",
        "no relevant information", "i don't know",
    ))


class BigQueryJudgeMetricsSink:
    """Optional BigQuery sink; local deployments remain dependency-free."""

    def __init__(self, table: str):
        self.table = table

    def write(self, row: dict[str, Any]) -> None:
        from google.cloud import bigquery

        bigquery.Client().insert_rows_json(self.table, [row])


__all__ = [
    "BigQueryJudgeMetricsSink", "CalibrationThresholds", "JudgeDecision",
    "JudgeRetrieveAbstainResult", "answer_overlap_confidence",
    "calibrate_thresholds", "finalize_after_retrieval", "judge_without_retrieval",
]
