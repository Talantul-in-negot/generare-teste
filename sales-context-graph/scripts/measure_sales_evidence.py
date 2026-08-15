"""Record reproducible local evidence for the sales extension.

This is intentionally an offline benchmark: it measures the deterministic MCP
registry and local CRM emulator only. External CRM, Neo4j, Redis and production
outcomes are recorded as null rather than inferred.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# ruff: noqa: E402
from src.domain.sales import SalesCRMWrite
from src.mcp.registry import discover
from src.sales.adapter import LocalCRMEmulator

OUT = ROOT / "artifacts" / "sales-evidence-latest.json"


def _percentile(values: list[float], percentile: float) -> float:
    values = sorted(values)
    index = min(len(values) - 1, round((len(values) - 1) * percentile))
    return round(values[index], 6)


def _command(command_id: str, version: int) -> SalesCRMWrite:
    return SalesCRMWrite(
        command_id=command_id, workspace_id="ws-measurement", actor_id="benchmark",
        capability="sales.opportunity.update", object_id="opp-1",
        patch={"amount_cents": 1000}, expected_version=version,
        approved=True, correlation_id=f"corr-{command_id}",
    )


def benchmark_local_adapter(iterations: int = 1000) -> dict:
    latencies: list[float] = []
    crm = LocalCRMEmulator()
    crm.seed(workspace_id="ws-measurement", object_id="opp-1", values={"amount_cents": 0})
    for index in range(iterations):
        start = time.perf_counter()
        crm.execute(_command(f"measurement-{index}", index + 1))
        latencies.append((time.perf_counter() - start) * 1000)
    return {
        "iterations": iterations,
        "throughput_ops_per_second": round(iterations / (sum(latencies) / 1000), 3),
        "latency_ms": {"p50": _percentile(latencies, 0.50), "p95": _percentile(latencies, 0.95), "p99": _percentile(latencies, 0.99)},
        "environment": "local in-memory synthetic emulator",
    }


def run_command(command: list[str], timeout: int = 120) -> dict:
    start = time.perf_counter()
    try:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=timeout)  # noqa: S603
        return {"command": command, "exit_code": result.returncode,
                "duration_seconds": round(time.perf_counter() - start, 3),
                "stdout_tail": result.stdout[-2000:], "stderr_tail": result.stderr[-2000:]}
    except subprocess.TimeoutExpired as exc:
        return {"command": command, "exit_code": None, "timed_out": True,
                "duration_seconds": round(time.perf_counter() - start, 3),
                "stdout_tail": (exc.stdout or "")[-2000:], "stderr_tail": (exc.stderr or "")[-2000:]}


def main() -> int:
    report = {
        "schema_version": "sales-evidence.v1",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "classification": "local benchmark; not production evidence",
        "checks": [
            run_command([sys.executable, "-m", "ruff", "check", "src/sales", "src/mcp", "tests/unit/sales", "tests/unit/test_mcp_registry.py"]),
            run_command([sys.executable, "-c", "from src.mcp.registry import discover; assert discover(scopes={'sales:read'}, workspace_id='ws-measurement'); from src.sales.adapter import LocalCRMEmulator; from src.domain.sales import SalesCRMWrite; c=LocalCRMEmulator(); c.seed(workspace_id='ws-measurement', object_id='opp-1', values={'stage':'PROPOSAL'}); x=SalesCRMWrite(command_id='smoke', workspace_id='ws-measurement', actor_id='smoke', capability='sales.opportunity.update', object_id='opp-1', patch={'stage':'NEGOTIATION'}, expected_version=1, approved=True, correlation_id='smoke'); assert len(c.execute(x).receipt_hash) == 64"], timeout=30),
        ],
        "full_test_suite": {"status": "not_run_in_measurement_script", "result": None},
        "mcp_discovery": {"workspace": "ws-measurement", "read_capabilities": len(discover(scopes={"sales:read"}, workspace_id="ws-measurement")), "write_capabilities_without_scope": len(discover(scopes=set(), workspace_id="ws-measurement"))},
        "local_crm_emulator": benchmark_local_adapter(),
        "external_or_production": {"neo4j": None, "redis": None, "remote_mcp": None, "production_latency": None, "availability": None, "cost": None, "business_outcomes": None},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(OUT)
    return 0 if all(item.get("exit_code") == 0 for item in report["checks"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
