"""Tests for RAGAS runner scoring and retry semantics."""

import math
from types import SimpleNamespace
from unittest.mock import AsyncMock

from scripts.run_faithfulness_eval import _evaluate_with_retries, _is_refusal


async def test_retries_non_finite_faithfulness_until_a_score_is_available():
    evaluator = SimpleNamespace(
        evaluate_single=AsyncMock(
            side_effect=[
                SimpleNamespace(faithfulness=math.nan),
                SimpleNamespace(faithfulness=0.9),
            ]
        )
    )

    result, attempts, notes = await _evaluate_with_retries(
        evaluator, "Q-1", "Question", "Answer", ["Context"],
    )

    assert result.faithfulness == 0.9
    assert attempts == 2
    assert notes == ["attempt 1: non-finite faithfulness"]


def test_refusal_detection_requires_a_marker_or_missing_context():
    assert _is_refusal("The provided context does not contain that fact.", ["context"])
    assert _is_refusal("An answer", [])
    assert not _is_refusal("The directive applies to Boeing 737 MAX.", ["context"])
