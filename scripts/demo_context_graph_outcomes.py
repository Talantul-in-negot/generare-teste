"""Demonstrate the audited Context Graph outcome → feedback → precedent loop."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from graphrag.context_graph.models import CGAction, CGFeedback, CGOutcome, OutcomeStatus
from graphrag.context_graph.repository import ContextGraphRepository
from graphrag.graph.neo4j_client import get_neo4j


async def run(args: argparse.Namespace) -> dict:
    repository = ContextGraphRepository(get_neo4j())
    suffix = uuid4().hex[:12]
    action = CGAction(
        id=f"demo-action-{suffix}", tenant=args.tenant, decision_id=args.decision_id,
        action_type="operational_follow_up", actor_id=args.actor_id,
        status="completed", reason_code="demo_outcome_loop",
    )
    outcome = CGOutcome(
        id=f"demo-outcome-{suffix}", tenant=args.tenant, action_id=action.id,
        outcome_type="operational_result", status=OutcomeStatus.OBSERVED,
        value={"demo": True, "result": args.result},
    )
    feedback = CGFeedback(
        id=f"demo-feedback-{suffix}", tenant=args.tenant, decision_id=args.decision_id,
        outcome_id=outcome.id, actor_id=args.actor_id, score=args.score,
        reason_code="human_reviewed", rationale="Demo reviewer score tied to an observed outcome.",
    )
    try:
        await repository.record_action(action)
        await repository.record_outcome(outcome)
        await repository.record_feedback(feedback)
        precedents = await repository.find_precedents(args.tenant, args.policy_version_id)
        return {
            "action_id": action.id,
            "outcome_id": outcome.id,
            "feedback_id": feedback.id,
            "relationships": ["CGDecision-RESULTED_IN->CGAction", "CGAction-PRODUCED->CGOutcome", "CGFeedback-ASSESSES->CGOutcome"],
            "precedents": precedents,
        }
    finally:
        await get_neo4j().close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--decision-id", required=True, help="Existing tenant-scoped Context Graph decision")
    parser.add_argument("--policy-version-id", required=True, help="Policy applied by that decision")
    parser.add_argument("--actor-id", default="demo-reviewer")
    parser.add_argument("--score", type=float, default=1.0, choices=[0.0, 0.25, 0.5, 0.75, 1.0])
    parser.add_argument("--result", default="successful_follow_up")
    print(json.dumps(asyncio.run(run(parser.parse_args())), indent=2, default=str))


if __name__ == "__main__":
    main()
