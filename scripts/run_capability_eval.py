"""Run deterministic safety regressions for the agent skill router.

The golden set deliberately includes denied and clarification cases. A routing
regression is not only a wrong successful answer; exposing a write capability
to an unentitled caller is a failure as well.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# `python scripts/run_capability_eval.py` sets sys.path to scripts/, unlike
# module execution. Make the repository package imports work in both forms.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from graphrag.agents.skill_router import default_skill_router
from mcp_server.capabilities import build_registry
from mcp_server.identity import CallerIdentity


DEFAULT_DATASET = Path("evals/capability_router_golden.json")


def evaluate(cases: list[dict]) -> dict:
    registry = build_registry()
    router = default_skill_router()
    results = []
    for case in cases:
        identity = CallerIdentity(
            subject="capability-eval", tenant="aerospace",
            scopes=frozenset(case["scopes"]), token_type="m2m", authenticated=True,
        )
        route = router.route(case["request"], registry.discover(identity))
        actual = route.model_dump()
        passed = all(actual[key] == case[key] for key in ("outcome", "skill_id", "capability_sequence"))
        results.append({"id": case["id"], "passed": passed, "actual": actual})
    return {
        "total": len(results),
        "passed": sum(item["passed"] for item in results),
        "failed": [item for item in results if not item["passed"]],
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", nargs="?", type=Path, default=DEFAULT_DATASET)
    args = parser.parse_args()
    report = evaluate(json.loads(args.dataset.read_text(encoding="utf-8")))
    print(json.dumps(report, indent=2))
    if report["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
