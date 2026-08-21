"""Stop and restore one Compose dependency, measuring real recovery time.

What changed and why
--------------------
The previous version recorded ``stopped_seconds`` — the wall time of the
``docker compose stop`` call itself — and declared recovery when the container
reached ``running``. Neither is a recovery-time measurement:

- ``stop`` duration measures Docker, not the outage.
- A container is ``running`` the instant its process starts. Neo4j reaches
  ``running`` many seconds before it will answer a query, and RabbitMQ before
  it will accept a publish. Calling that "recovered" reports an RTO several
  times better than the truth, in the optimistic direction — the worst way for
  a reliability number to be wrong.

This version waits for the service's Compose **healthcheck** to report
``healthy`` (all three dependencies define real ones: ``redis-cli ping``,
``cypher-shell RETURN 1``, ``rabbitmq-diagnostics ping``) and reports the
interval from "start issued" to "dependency usable" as ``recovery_seconds``.

``rabbitmq`` is now selectable. It was missing, and it is the dependency whose
restart matters most: it owns durable queue topology and in-flight messages, so
its recovery exercises reconnection and redelivery rather than just a cache
refill.

What this does and does not prove
---------------------------------
It measures single-node dependency restart on one developer machine. It is not
an availability figure, not a production RTO, and says nothing about data loss
(RPO) — a healthcheck answering does not mean nothing was dropped. Treat the
number as a floor for local recovery, not as a service-level claim.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

SERVICES = ("redis", "neo4j", "rabbitmq")

# Generous: Neo4j cold start on a laptop is routinely 30-60s, and a ceiling
# that trips before a healthy service returns would report a false failure.
DEFAULT_RECOVERY_TIMEOUT = 180


def _compose(compose_file: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["docker", "compose", "-f", str(compose_file), *args],
        check=check, capture_output=True, text=True,
    )
    return (result.stdout or "").strip()


def _health(compose_file: Path, service: str) -> str:
    """Return the service's health state, or its container state if unhealthy-unaware.

    `docker compose ps --format json` emits one JSON object per line (or a JSON
    array, depending on the Compose version), so both shapes are handled.
    """
    raw = _compose(compose_file, "ps", "--format", "json", service, check=False)
    if not raw:
        return "absent"
    entries: list[dict] = []
    try:
        parsed = json.loads(raw)
        entries = parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    for entry in entries:
        if entry.get("Service") != service:
            continue
        # Health is "" for a container with no healthcheck; fall back to State
        # so the script still reports something meaningful there.
        return str(entry.get("Health") or entry.get("State") or "unknown")
    return "absent"


def _wait_for_health(compose_file: Path, service: str, timeout: int) -> tuple[bool, float, str]:
    """Block until `service` reports healthy. Returns (ok, seconds, last state)."""
    started = time.perf_counter()
    deadline = started + timeout
    state = ""
    while time.perf_counter() < deadline:
        state = _health(compose_file, service)
        if state == "healthy":
            return True, round(time.perf_counter() - started, 3), state
        # "running" without a healthcheck is the best signal available; accept
        # it only when the service genuinely defines none.
        if state == "running" and not _has_healthcheck(compose_file, service):
            return True, round(time.perf_counter() - started, 3), state
        time.sleep(1)
    return False, round(time.perf_counter() - started, 3), state


def _has_healthcheck(compose_file: Path, service: str) -> bool:
    config = _compose(compose_file, "config", "--format", "json", check=False)
    try:
        services = json.loads(config).get("services", {})
    except (json.JSONDecodeError, AttributeError):
        return False
    return "healthcheck" in (services.get(service) or {})


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose-file", type=Path, default=root / "compose.dev.yaml")
    parser.add_argument("--service", default="redis", choices=SERVICES)
    parser.add_argument("--timeout", type=int, default=DEFAULT_RECOVERY_TIMEOUT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    report: dict = {
        "report_schema_version": "docker-failure-exercise/v2",
        "service": args.service,
        "recovery_timeout_seconds": args.timeout,
    }

    health_before = _health(args.compose_file, args.service)
    report["health_before"] = health_before
    if health_before != "healthy":
        # Restarting an already-broken dependency measures nothing.
        report["error"] = (
            f"{args.service} was {health_before!r} before the exercise; "
            f"bring the stack up and healthy first"
        )
        _write(args.output, report)
        raise SystemExit(1)

    outage_started = time.perf_counter()
    try:
        _compose(args.compose_file, "stop", args.service)
        report["stop_observed"] = _health(args.compose_file, args.service) != "healthy"
    finally:
        # Always attempt restart, even if the stop or the assertion above
        # raised: leaving a developer's stack down is a worse outcome than a
        # missing measurement.
        _compose(args.compose_file, "start", args.service)

    recovered, recovery_seconds, last_state = _wait_for_health(
        args.compose_file, args.service, args.timeout,
    )
    total_outage = round(time.perf_counter() - outage_started, 3)

    report.update({
        "restart_observed": recovered,
        "health_after": last_state,
        # Time from `start` being issued to the dependency answering its own
        # healthcheck -- the closest thing to a recovery time this exercise
        # can honestly report.
        "recovery_seconds": recovery_seconds,
        # Includes the deliberate stop, so it is an upper bound on the window
        # in which the dependency was unusable.
        "total_outage_seconds": total_outage,
        "claim_policy": (
            "Single-node dependency stop/start on one machine, measured to "
            "healthcheck-healthy. Not an availability figure, not a production "
            "RTO, and not an RPO claim -- a healthy probe does not prove "
            "nothing was lost."
        ),
    })
    _write(args.output, report)
    if not recovered:
        raise SystemExit(1)


def _write(output: Path, report: dict) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
