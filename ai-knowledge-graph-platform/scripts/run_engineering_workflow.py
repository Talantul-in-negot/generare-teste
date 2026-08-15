"""Run a repository-local specification-to-implementation workflow.

Examples:
  python scripts/run_engineering_workflow.py workflows/example.yaml
  python scripts/run_engineering_workflow.py workflows/example.yaml --approve --run-id RUN_ID --resume
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graphrag.engineering_workflows import WorkflowOrchestrator


async def _run(args: argparse.Namespace) -> None:
    root = Path(__file__).resolve().parents[1]
    spec_path = (root / args.spec).resolve()
    orchestrator = WorkflowOrchestrator(root, state_dir=root / args.state_dir)
    spec = orchestrator.load_spec(spec_path)
    result = await orchestrator.run(
        spec,
        run_id=args.run_id,
        approval_granted=args.approve,
        resume=args.resume,
    )
    print(result.model_dump_json(indent=2))
    if result.status.value in {"failed"}:
        raise SystemExit(1)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Run a specification-to-implementation workflow")
    parser.add_argument("spec", help="YAML workflow specification relative to the repository root")
    parser.add_argument("--run-id", help="Resume or assign a stable run ID")
    parser.add_argument("--resume", action="store_true", help="Resume completed tasks from persisted state")
    parser.add_argument("--approve", action="store_true", help="Approve tasks requiring human review")
    parser.add_argument("--state-dir", default=".workflows/runs", help="State directory relative to the repository root")
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
