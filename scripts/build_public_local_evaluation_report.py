"""Build a source-linked public report from observed local evidence artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, default=Path("artifacts"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    artifacts = args.artifacts
    retrieval = _read(artifacts / "graph-fact-golden-eval.json")
    load = _read(artifacts / "mcp-graph-fact-load-matrix.json") if (artifacts / "mcp-graph-fact-load-matrix.json").exists() else _read(artifacts / "mcp-graph-fact-load.json")
    writes = _read(artifacts / "governed-write-evidence.json")
    failure = _read(artifacts / "local-failure-exercises.json") if (artifacts / "local-failure-exercises.json").exists() else None
    warm = _read(artifacts / "mcp-warm-session-benchmark.json") if (artifacts / "mcp-warm-session-benchmark.json").exists() else None
    scenarios = load.get("scenarios", [load])
    lines = [
        "# Public Local Evaluation Report", "",
        "## Scope", "",
        "- Environment: local Docker Compose (MCP, API, Neo4j)",
        "- Data: fixed synthetic `local-evidence` tenant", "- Claim boundary: local reproducibility evidence only; no production or customer outcomes.", "",
        "## Observed results", "",
        "| Measure | Result | Evidence |",
        "|---|---:|---|",
        f"| Graph-fact retrieval pass rate | {retrieval['candidate']['pass_rate']:.0%} ({retrieval['candidate']['questions']} fixed questions) | `artifacts/graph-fact-golden-eval.json` |",
        f"| Empty-corpus baseline pass rate | {retrieval['baseline']['pass_rate']:.0%} | `data/evidence/graph-fact-golden.json` |",
        *[f"| MCP load ({item['total']} requests) | {item['passed']}/{item['total']} passed; {item['throughput_rps']:.2f} req/s; p95 {item['p95_latency_ms']:.2f} ms | `artifacts/mcp-graph-fact-load-matrix.json` |" for item in scenarios],
        *( [f"| Local failure-control matrix | {failure['passed']}/{failure['passed'] + failure['failed']} scenarios passed | `artifacts/local-failure-exercises.json` |"] if failure else [] ),
        *( [f"| Warm MCP session load | {warm['requests']}/{warm['requests'] + warm['failed']} passed; {warm['throughput_rps']:.2f} req/s; p95 {warm['p95_latency_ms']:.2f} ms | `artifacts/mcp-warm-session-benchmark.json` |"] if warm else [] ),
        "", "## Governed operational write cases", "",
        "- Approval gate: " + writes["write_approval_requested"]["outcome"],
        "- Approved write: " + writes["write_executed"]["outcome"],
        "- Idempotent replay: " + writes["idempotent_replay"]["outcome"],
        "- Stale-version protection: " + writes["stale_version_refusal"]["outcome"],
        "- Dry-run preview: " + writes["dry_run"]["outcome"],
        "- Approval-gated compensation: " + writes["compensated"]["outcome"],
        "", "## Reproduction", "",
        "See `docs/local-evidence-runbook.md`. Re-run the seed, governed-write, golden-eval, and load commands against a clean `local-evidence` tenant.",
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
