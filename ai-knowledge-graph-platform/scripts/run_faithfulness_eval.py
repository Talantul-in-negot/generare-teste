"""
Full 40-question faithfulness eval — Groq as primary RAGAS judge.

Run this after Groq daily quota resets (midnight UTC).
Results written to evals/faithfulness_eval_results.json.
"""
import asyncio
import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

# Fail fast on a wrong interpreter (e.g. another project's venv on PATH).
# Without this, retrieval works but every RAGAS judge call errors out and a
# 15-minute run produces a results file full of errors instead of scores.
try:
    import datasets  # noqa: F401
except ImportError as _exc:
    sys.exit(
        f"Missing eval dependency ({_exc.name}) — wrong Python interpreter?\n"
        f"  running : {sys.executable}\n"
        f"  expected: the project's Python 3.11 with requirements installed"
    )

# Do not import ``ragas`` here.  ragas 0.4 imports the removed
# ``langchain_community.chat_models.vertexai`` module at import time; the
# compatibility stub for that optional integration is deliberately installed
# by RagasEvaluator immediately before metrics are loaded.

_REFUSAL = (
    "does not contain",
    "no information",
    "not specify",
    "cannot find",
    "not mentioned",
    "sufficient information",
    "not available",
    "no details",
)
_RAGAS_ATTEMPTS = max(1, int(os.getenv("RAGAS_EVAL_ATTEMPTS", "3")))


def _is_refusal(answer: str, contexts: list[str]) -> bool:
    """Return whether retrieval intentionally abstained from answering."""
    return not contexts or any(marker in answer.lower() for marker in _REFUSAL)


async def _evaluate_with_retries(
    evaluator, query_id: str, question: str, answer: str, contexts: list[str],
) -> tuple[object | None, int, list[str]]:
    """Retry transient/non-finite RAGAS results without hiding their cause."""
    notes: list[str] = []
    last_result = None
    for attempt in range(1, _RAGAS_ATTEMPTS + 1):
        try:
            result = await evaluator.evaluate_single(
                query_id, question, answer, contexts, ""
            )
            last_result = result
            faithfulness = result.faithfulness
            if isinstance(faithfulness, (int, float)) and math.isfinite(faithfulness):
                return result, attempt, notes
            notes.append(f"attempt {attempt}: non-finite faithfulness")
        except Exception as exc:
            notes.append(f"attempt {attempt}: {type(exc).__name__}: {str(exc)[:160]}")

        # RAGAS itself performs prompt retries. This small delay is only for
        # the outer retry, allowing a saturated judge provider to recover.
        if attempt < _RAGAS_ATTEMPTS:
            await asyncio.sleep(attempt)
    return last_result, _RAGAS_ATTEMPTS, notes


def _contract_result(question: dict, answer: str, citations: list[str]) -> tuple[bool, list[str]]:
    """Use the deterministic golden contract for every evaluated response."""
    from scripts.run_golden_eval import _check

    return _check(question, {"answer": answer, "citations": citations})


async def main(question_ids: set[str] | None = None):
    from graphrag.retrieval.hybrid_retriever import HybridRetriever
    from graphrag.evaluation.ragas_evaluator import RagasEvaluator
    from graphrag.evaluation.judge_retrieve_abstain import (
        CalibrationThresholds,
        JudgeDecision,
        finalize_after_retrieval,
        judge_without_retrieval,
    )
    from graphrag.core.config import get_settings
    from graphrag.core.models import EvalResult
    from graphrag.evidence.claim_graph import (
        build_claim_evidence_graph,
        persist_claim_evidence_graph,
    )
    from graphrag.graph.confidence_calibration import CalibrationService
    from graphrag.graph.neo4j_client import get_neo4j

    golden = json.loads((Path(__file__).parents[1] / "evals" / "golden_set.json").read_text())
    questions = golden["questions"]
    if question_ids is not None:
        questions = [q for q in questions if q["id"] in question_ids]
        if not questions:
            raise ValueError("No golden questions matched --ids")

    retriever = HybridRetriever()
    evaluator = RagasEvaluator()
    neo4j = get_neo4j()
    cal_svc = CalibrationService(neo4j)
    tenant = "aerospace"
    judge_cfg = get_settings().evaluation.get("judge_retrieve_abstain", {})
    judge_thresholds = CalibrationThresholds(
        accept_threshold=float(judge_cfg.get("accept_threshold", 0.90)),
        retrieve_threshold=float(judge_cfg.get("retrieve_threshold", 0.55)),
        target_fdr=float(judge_cfg.get("target_fdr", 0.05)),
    )
    retrieval_accept_threshold = float(judge_cfg.get("retrieval_accept_threshold", 0.80))

    scores, correct_abstentions, refusal_failures, errors, unscorable = [], 0, 0, 0, 0
    judge_accepts, judge_retrievals, judge_abstentions = 0, 0, 0
    contract_passes = 0
    results = []
    cal_samples = 0

    async def persist_claim_graph(result, evaluation: EvalResult) -> None:
        """Keep the standalone evaluator on the same provenance path as workers."""
        try:
            graph = build_claim_evidence_graph(result, evaluation, tenant=tenant)
            await persist_claim_evidence_graph(neo4j, graph)
        except Exception as exc:
            # Provenance is best effort for a batch report; the score remains
            # useful when Neo4j is temporarily unavailable.
            print(f"  Warning: claim graph persistence failed: {str(exc)[:160]}")

    print(f"\n{'='*70}")
    print(f"  GraphRAG Faithfulness Eval — {len(questions)} questions")
    print(f"  Started: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*70}\n")

    for q in questions:
        t0 = time.monotonic()
        try:
            res = await retriever.retrieve_and_answer(question=q["question"], tenant="aerospace")
            elapsed = time.monotonic() - t0
            contract_ok, contract_failures = _contract_result(q, res.answer, res.citations)
            initial_policy = judge_without_retrieval(
                answer=res.answer, reference=q.get("ground_truth", ""),
                thresholds=judge_thresholds,
            )
            if initial_policy.decision == JudgeDecision.RETRIEVE:
                judge_retrievals += 1
            if contract_ok:
                contract_passes += 1

            if _is_refusal(res.answer, res.contexts):
                if contract_ok:
                    correct_abstentions += 1
                    status = "correct_abstention"
                    label = "CORRECT ABSTENTION"
                else:
                    refusal_failures += 1
                    status = "refusal_failure"
                    label = "REFUSAL FAILURE"
                print(f"  [{q['id']:8s}] {label}  ({elapsed:.1f}s)  {res.answer[:60]!r}")
                results.append({"id": q["id"], "type": q["type"], "status": status,
                                 "answer": res.answer, "citations": res.citations,
                                 "golden_contract_pass": contract_ok,
                                 "golden_contract_failures": contract_failures,
                                 "judge_decision": initial_policy.decision.value,
                                 "judge_confidence": initial_policy.confidence,
                                 "retrieval_used": False,
                                 "abstention_reason": initial_policy.abstention_reason,
                                 "latency": round(elapsed, 1)})
                await persist_claim_graph(res, EvalResult(
                    job_id=q["id"], query_id=res.query_id,
                    judge_decision=initial_policy.decision.value,
                    judge_confidence=initial_policy.confidence,
                    judge_accept_threshold=judge_thresholds.accept_threshold,
                    judge_retrieve_threshold=judge_thresholds.retrieve_threshold,
                    judge_target_fdr=judge_thresholds.target_fdr,
                    retrieval_used=False,
                    abstention_reason=initial_policy.abstention_reason,
                    evaluation_source="judge",
                ))
                judge_abstentions += 1
                continue

            er, attempts, judge_notes = await _evaluate_with_retries(
                evaluator, q["id"], q["question"], res.answer, res.contexts,
            )
            f = er.faithfulness if er is not None else float("nan")

            # RAGAS returns NaN (not an exception) when its claim-decomposition
            # step can't extract any verifiable statements from the answer —
            # this happens routinely on short/terse or yes-no answers. NaN is a
            # legitimate "metric not applicable" signal, not a faithfulness
            # violation, so it must be excluded the same way a refusal is —
            # previously it was appended straight into `scores` and silently
            # poisoned the whole average via `sum()` (sum of anything with a
            # NaN in it is NaN), and was also fed into the calibration sample
            # below as an `actual_outcome`, corrupting the Brier-score dataset.
            if isinstance(f, float) and math.isnan(f):
                unscorable += 1
                final_policy = finalize_after_retrieval(
                    initial_policy, None, accept_threshold=retrieval_accept_threshold,
                )
                judge_abstentions += 1
                print(f"  [{q['id']:8s}] UNSCORABLE after {attempts} attempt(s) — "
                      f"RAGAS could not produce finite faithfulness  ({elapsed:.1f}s)  "
                      f"{res.answer[:60]!r}")
                results.append({"id": q["id"], "type": q["type"], "status": "unscorable",
                                 "answer": res.answer, "citations": res.citations,
                                 "golden_contract_pass": contract_ok,
                                 "golden_contract_failures": contract_failures,
                                 "judge_decision": final_policy.decision.value,
                                 "judge_confidence": final_policy.confidence,
                                 "judge_accept_threshold": retrieval_accept_threshold,
                                 "judge_retrieve_threshold": judge_thresholds.retrieve_threshold,
                                 "judge_target_fdr": judge_thresholds.target_fdr,
                                 "retrieval_used": True,
                                 "abstention_reason": final_policy.abstention_reason,
                                 "evaluation_attempts": attempts,
                                 "judge_notes": judge_notes,
                                 "latency": round(elapsed, 1)})
                await persist_claim_graph(res, (er or EvalResult(
                    job_id=q["id"], query_id=res.query_id,
                )).model_copy(update={
                    "judge_decision": final_policy.decision.value,
                    "judge_confidence": final_policy.confidence,
                    "judge_accept_threshold": retrieval_accept_threshold,
                    "judge_retrieve_threshold": judge_thresholds.retrieve_threshold,
                    "judge_target_fdr": judge_thresholds.target_fdr,
                    "retrieval_used": True,
                    "abstention_reason": final_policy.abstention_reason,
                    "evaluation_source": "ragas",
                }))
                continue

            scores.append(f)
            final_policy = finalize_after_retrieval(
                initial_policy, f, accept_threshold=retrieval_accept_threshold,
            )
            if final_policy.decision == JudgeDecision.ACCEPT:
                judge_accepts += 1
            elif final_policy.decision == JudgeDecision.ABSTAIN:
                judge_abstentions += 1
            evaluation_for_graph = er.model_copy(update={
                "judge_decision": final_policy.decision.value,
                "judge_confidence": final_policy.confidence,
                "judge_accept_threshold": retrieval_accept_threshold,
                "judge_retrieve_threshold": judge_thresholds.retrieve_threshold,
                "judge_target_fdr": judge_thresholds.target_fdr,
                "retrieval_used": True,
                "abstention_reason": final_policy.abstention_reason,
                "evaluation_source": "ragas",
            })
            # Append result before print — print errors (e.g. Windows encoding) must not discard score
            results.append({"id": q["id"], "type": q["type"], "status": "scored",
                             "faithfulness": round(f, 4), "answer": res.answer,
                             "citations": res.citations,
                             "golden_contract_pass": contract_ok,
                             "golden_contract_failures": contract_failures,
                             "judge_decision": final_policy.decision.value,
                             "judge_confidence": final_policy.confidence,
                             "judge_accept_threshold": retrieval_accept_threshold,
                             "judge_retrieve_threshold": judge_thresholds.retrieve_threshold,
                             "judge_target_fdr": judge_thresholds.target_fdr,
                             "retrieval_used": True,
                             "abstention_reason": final_policy.abstention_reason,
                             "evaluation_attempts": attempts,
                             "judge_notes": judge_notes,
                             "latency": round(elapsed, 1)})
            await persist_claim_graph(res, evaluation_for_graph)
            flag = "  LOW" if (not isinstance(f, float) or f < 0.8) else ""
            print(f"  [{q['id']:8s}] faith={f:.3f}  ({elapsed:.1f}s)  {res.answer[:60]!r}{flag}")

            # Calibration requires both metrics.  Never turn a missing/NaN
            # context-precision result into a misleading zero-confidence sample.
            if (isinstance(er.context_precision, (int, float))
                    and math.isfinite(er.context_precision)):
                try:
                    await cal_svc.add_sample(
                        predicted_confidence=er.context_precision,
                        actual_outcome=f,
                        relation=q.get("type", ""),
                        source_doc_id=q["id"],
                        prompt_version="run_faithfulness_eval",
                        tenant=tenant,
                        verified_by="ragas",
                    )
                    cal_samples += 1
                except Exception:
                    pass  # calibration failure must not abort the eval

        except Exception as e:
            errors += 1
            print(f"  [{q['id']:8s}] ERROR: {e}")
            results.append({"id": q["id"], "type": q["type"], "status": "error", "error": str(e)})

    # Summary
    avg = sum(scores) / len(scores) if scores else 0.0
    by_type: dict = {}
    for r in results:
        by_type.setdefault(r["type"], []).append(r)

    print(f"\n{'='*70}")
    coverage = len(scores) / len(questions) if questions else 0.0
    lower_bound = sum(scores) / len(questions) if questions else 0.0
    print(f"  Faithfulness (RAGAS-scored {len(scores)}/{len(questions)}): {avg:.3f}")
    print(f"  Baseline: 0.840   Delta: {avg - 0.840:+.3f}")
    print(f"  RAGAS coverage: {coverage:.1%}")
    print(f"  Faithfulness lower bound (unscored = 0): {lower_bound:.3f}")
    print(f"  Golden contract pass rate: {contract_passes}/{len(questions)} "
          f"({contract_passes / len(questions):.1%})")
    print(f"  Correct abstentions: {correct_abstentions}/{len(questions)}")
    print(f"  Judge decisions: accept={judge_accepts}, retrieve={judge_retrievals}, "
          f"abstain={judge_abstentions}")
    if refusal_failures:
        print(f"  Retrieval refusal failures: {refusal_failures}/{len(questions)}")
    if unscorable:
        print(f"  Unscorable (RAGAS NaN, excluded): {unscorable}/{len(questions)}")
    if errors:
        print(f"  Errors: {errors}")
    print("\n  By question type:")
    for qtype, rows in sorted(by_type.items()):
        type_scores = [r["faithfulness"] for r in rows if r.get("status") == "scored"]
        type_refusals = sum(1 for r in rows if r.get("status") == "refusal")
        if type_scores:
            print(f"    {qtype:20s}  faith={sum(type_scores)/len(type_scores):.3f}  "
                  f"scored={len(type_scores)}  refusals={type_refusals}")
    print(f"{'='*70}\n")

    # Write results
    output_name = (
        "faithfulness_eval_targeted_results.json"
        if question_ids is not None
        else "faithfulness_eval_results.json"
    )
    out = Path(__file__).parents[1] / "evals" / output_name
    out.write_text(json.dumps({
        "run_at": datetime.now(timezone.utc).isoformat(),
        "scope": "targeted" if question_ids is not None else "full",
        "question_ids": [q["id"] for q in questions],
        "faithfulness_answerable": round(avg, 4),
        "ragas_coverage": round(coverage, 4),
        "faithfulness_lower_bound": round(lower_bound, 4),
        "golden_contract_pass_rate": round(contract_passes / len(questions), 4),
        "n_golden_contract_passed": contract_passes,
        "baseline": 0.840,
        "delta": round(avg - 0.840, 4),
        "n_scored": len(scores),
        "n_correct_abstentions": correct_abstentions,
        "n_refusal_failures": refusal_failures,
        "n_judge_accepts": judge_accepts,
        "n_judge_retrievals": judge_retrievals,
        "n_judge_abstentions": judge_abstentions,
        "judge_target_fdr": judge_thresholds.target_fdr,
        "judge_accept_threshold": retrieval_accept_threshold,
        "judge_retrieve_threshold": judge_thresholds.retrieve_threshold,
        "n_unscorable": unscorable,
        "n_errors": errors,
        "n_total": len(questions),
        "by_type": {
            qtype: {
                "faithfulness": round(
                    sum(r["faithfulness"] for r in rows if r.get("status") == "scored") /
                    max(1, sum(1 for r in rows if r.get("status") == "scored")), 4
                ),
                "scored": sum(1 for r in rows if r.get("status") == "scored"),
                "refusals": sum(
                    1
                    for r in rows
                    if r.get("status") in {"correct_abstention", "refusal_failure"}
                ),
            }
            for qtype, rows in by_type.items()
        },
        "questions": results,
    }, indent=2))
    print(f"  Results written to: {out}")

    # ── Calibration snapshot: persist aggregate metrics for the dashboard trend chart
    if cal_samples > 0:
        try:
            snap_id = await cal_svc.persist_snapshot(
                tenant=tenant,
                label=f"faithfulness-eval-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}",
            )
            print(f"  Calibration snapshot written ({cal_samples} samples): {snap_id[:8]}...")
        except Exception as _snap_exc:
            print(f"  Warning: calibration snapshot failed: {_snap_exc}")
    await neo4j.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run GraphRAG RAGAS faithfulness evaluation")
    parser.add_argument("--ids", nargs="+", help="Run only specified golden-question IDs")
    args = parser.parse_args()
    asyncio.run(main(set(args.ids) if args.ids else None))
