from graphrag.evaluation.judge_retrieve_abstain import (
    CalibrationThresholds,
    JudgeDecision,
    calibrate_thresholds,
    finalize_after_retrieval,
    judge_without_retrieval,
)


def test_calibration_honors_false_discovery_rate_target():
    thresholds = calibrate_thresholds([
        {"confidence": 0.95, "correct": True},
        {"confidence": 0.90, "correct": True},
        {"confidence": 0.85, "correct": False},
        {"confidence": 0.60, "correct": True},
    ], target_fdr=0.0)
    assert thresholds.accept_threshold == 0.9
    assert thresholds.target_fdr == 0.0


def test_uncertain_initial_judge_retrieves_then_abstains_on_weak_score():
    initial = judge_without_retrieval(
        answer="The answer is incomplete.",
        reference="FAA AD 2024-01-02 applies to Southwest.",
        thresholds=CalibrationThresholds(accept_threshold=0.9, retrieve_threshold=0.3),
    )
    assert initial.decision == JudgeDecision.RETRIEVE
    final = finalize_after_retrieval(initial, 0.2)
    assert final.decision == JudgeDecision.ABSTAIN
    assert final.abstention_reason == "retrieval_score_below_accept_threshold"


def test_refusal_is_explicit_abstention():
    result = judge_without_retrieval(answer="Insufficient context to answer this question.")
    assert result.decision == JudgeDecision.ABSTAIN
    assert result.abstention_reason == "empty_or_refusal_answer"


def test_retrieval_accept_threshold_can_be_calibrated_separately():
    initial = judge_without_retrieval(
        answer="A partial answer.", reference="A complete reference answer.",
        thresholds=CalibrationThresholds(accept_threshold=0.9, retrieve_threshold=0.2),
    )
    result = finalize_after_retrieval(initial, 0.82, accept_threshold=0.8)
    assert result.decision == JudgeDecision.ACCEPT
    assert result.thresholds.accept_threshold == 0.8
