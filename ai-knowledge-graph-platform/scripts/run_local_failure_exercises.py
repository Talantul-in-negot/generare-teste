"""Execute the local failure-control matrix and record what actually happened.

What this replaces
------------------
This script previously emitted six scenarios with a hardcoded ``"passed": True``
and ran nothing. Its output feeds
``docs/public-local-evaluation-report.md``, which published "Local
failure-control matrix | 6/6 scenarios passed" — a verification claim with no
verification behind it. A control that is asserted rather than exercised is
worse than an absent one: it occupies the space where evidence should be, and
it cannot fail, so nobody ever looks at it again.

Each scenario now names the test(s) that *prove* the control and runs them. The
report records the real outcome, the node ids, and pytest's exit code, so the
claim is auditable back to executable proof.

Why node ids rather than reimplemented checks
---------------------------------------------
The controls already have tests. Re-implementing the assertions here would
create a second, silently-diverging definition of each control — the report
would keep passing after the real behaviour regressed, which is the same
failure this rewrite exists to remove. Pointing at the tests keeps exactly one
definition.

Failure modes this script must not have
---------------------------------------
- A **missing or renamed** node id must fail loudly. pytest exits 4 on a bad
  argument and 5 when nothing is collected; both are treated as scenario
  failures, and the collected count is checked against the expected count so a
  silently-vanished test cannot read as success.
- A **passing run with zero tests** is a failure, not a pass.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from graphrag.evidence.reports import summarize_workflow_costs  # noqa: E402

# pytest's documented exit codes; 4 = usage error (e.g. unknown node id),
# 5 = no tests collected. Either means the control was not exercised.
_EXIT_OK = 0
_EXIT_NO_TESTS = 5


@dataclass(frozen=True)
class Scenario:
    """One failure control, and the tests that demonstrate it holds."""

    id: str
    expected: str
    claim: str
    node_ids: tuple[str, ...] = field(default_factory=tuple)


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        id="duplicate-idempotency",
        expected="same_receipt",
        claim="replaying the same write key returns the original receipt",
        node_ids=(
            "tests/unit/test_business_write_path.py::TestCreateFromFindingSuccess"
            "::test_repeat_call_with_same_command_id_short_circuits",
        ),
    ),
    Scenario(
        id="stale-optimistic-version",
        expected="refused",
        claim="an old expected version cannot overwrite newer state",
        node_ids=(
            "tests/unit/test_business_write_path.py::TestCreateFromFindingStaleVersion"
            "::test_stale_version_rejects_with_zero_writes_and_no_corpus_revision",
        ),
    ),
    Scenario(
        id="tenant-boundary",
        expected="denied",
        claim="an approval issued in one tenant cannot be reused in another",
        node_ids=(
            "tests/unit/test_business_write_path.py::TestCreateFromFindingApprovalFlow"
            "::test_cross_tenant_approval_reuse_rejected",
        ),
    ),
    Scenario(
        id="approval-bypass",
        expected="denied",
        claim="a write cannot be self-approved by its own requester",
        node_ids=(
            "tests/unit/test_business_write_path.py::TestCreateFromFindingApprovalFlow"
            "::test_approval_denied_when_decider_is_the_requester",
        ),
    ),
    Scenario(
        id="compensation-replay",
        expected="same_receipt",
        claim="compensation is approval-gated and idempotent",
        node_ids=(
            "tests/unit/test_workorder_compensation.py::TestWorkOrderCompensation"
            "::test_compensation_always_requires_human_approval",
            "tests/unit/test_workorder_compensation.py::TestWorkOrderCompensation"
            "::test_approved_compensation_is_atomic_and_receipted",
        ),
    ),
    Scenario(
        id="backup-restore-integrity",
        expected="digest_match",
        claim="a mutated snapshot fails integrity verification",
        node_ids=(
            "tests/unit/test_snapshot_integrity.py::TestTamperingIsDetected"
            "::test_a_mutated_field_fails_verification",
            "tests/unit/test_snapshot_integrity.py::TestVerifierCanActuallySayNo"
            "::test_it_is_not_a_constant_true",
        ),
    ),
)


def _run_scenario(scenario: Scenario) -> dict:
    """Execute one scenario's proving tests and report the real outcome."""
    started = time.perf_counter()
    completed = subprocess.run(
        [
            sys.executable, "-m", "pytest",
            "-q", "--no-header", "-p", "no:cacheprovider",
            "--tb=short",
            *scenario.node_ids,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    elapsed = round(time.perf_counter() - started, 3)
    stdout = completed.stdout or ""

    # A clean exit is necessary but not sufficient: a scenario that collected
    # nothing must not read as a pass. The exit code is the primary guard --
    # pytest exits 4 when any named node id cannot be resolved, which is what
    # a renamed or deleted proving test looks like (verified: one valid plus
    # one bogus id still exits 4). The count is the backstop for the
    # exited-clean-but-ran-nothing case.
    #
    # `>=` not `==`: a parametrised node id legitimately expands to many tests,
    # so requiring equality would fail a scenario for being *better* covered.
    collected = _collected_count(stdout)
    expected_count = len(scenario.node_ids)
    exercised = collected >= expected_count and collected > 0
    passed = completed.returncode == _EXIT_OK and exercised

    reason = ""
    if completed.returncode == _EXIT_NO_TESTS or collected == 0:
        reason = "no tests were collected — a proving test is missing or renamed"
    elif not exercised:
        reason = f"expected at least {expected_count} proving test(s), pytest ran {collected}"
    elif completed.returncode != _EXIT_OK:
        reason = "a proving test failed"

    return {
        "id": scenario.id,
        "expected": scenario.expected,
        "passed": passed,
        "evidence": scenario.claim,
        # The node ids make the claim auditable: a reader can rerun exactly
        # what produced this verdict.
        "proving_tests": list(scenario.node_ids),
        "tests_collected": collected,
        "tests_expected_minimum": expected_count,
        "pytest_exit_code": completed.returncode,
        "elapsed_seconds": elapsed,
        **({"failure_reason": reason} if reason else {}),
        **({"output_tail": stdout.strip().splitlines()[-12:]} if not passed else {}),
    }


def _collected_count(stdout: str) -> int:
    """Parse how many tests pytest actually ran from its summary line.

    Deliberately conservative: anything unparseable counts as zero, so an
    unexpected pytest output format fails the scenario rather than silently
    reporting success.
    """
    for line in reversed(stdout.strip().splitlines()):
        if " passed" not in line and " failed" not in line and " error" not in line:
            continue
        total = 0
        tokens = line.replace("=", " ").replace(",", " ").split()
        for index, token in enumerate(tokens):
            if token in ("passed", "failed", "error", "errors") and index:
                try:
                    total += int(tokens[index - 1])
                except ValueError:
                    continue
        if total:
            return total
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    started = time.perf_counter()
    scenarios = [_run_scenario(scenario) for scenario in SCENARIOS]

    report = {
        "report_schema_version": "local-failure-exercises/v2",
        "scenarios": scenarios,
        "passed": sum(item["passed"] for item in scenarios),
        "failed": sum(not item["passed"] for item in scenarios),
        "elapsed_seconds": round(time.perf_counter() - started, 6),
        "claim_policy": (
            "Every scenario is proven by the named tests, executed by this run. "
            "This is a control-matrix result for in-process behaviour only: "
            "dependency-interruption, recovery-time, and customer-incident "
            "claims require live deployment evidence "
            "(scripts/run_docker_failure_exercise.py and a real deployment)."
        ),
        "workflow_summary": summarize_workflow_costs(
            [{"run_id": item["id"], "status": "completed" if item["passed"] else "failed"}
             for item in scenarios],
            [],
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if report["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
