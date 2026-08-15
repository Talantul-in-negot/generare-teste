"""Stop and restore one local Compose dependency, recording recovery evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path


def _compose(compose_file: Path, *args: str) -> str:
    result = subprocess.run(
        ["docker", "compose", "-f", str(compose_file), *args],
        check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose-file", type=Path, default=root / "compose.dev.yaml")
    parser.add_argument("--service", default="redis", choices=["redis", "neo4j"])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = {"report_schema_version": "docker-failure-exercise/v1", "service": args.service}
    stopped_at = time.perf_counter()
    try:
        _compose(args.compose_file, "stop", args.service)
        report["stop_observed"] = True
        report["stopped_seconds"] = time.perf_counter() - stopped_at
    finally:
        _compose(args.compose_file, "start", args.service)
    deadline = time.time() + 60
    status = ""
    while time.time() < deadline:
        status = _compose(args.compose_file, "ps", "--status", "running", "--services")
        if args.service in status.splitlines():
            break
        time.sleep(1)
    report.update({
        "restart_observed": args.service in status.splitlines(),
        "running_services": status.splitlines(),
        "claim_policy": "Local Docker dependency stop/start and container recovery only; not incident prevention or availability evidence.",
    })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if not report["restart_observed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
