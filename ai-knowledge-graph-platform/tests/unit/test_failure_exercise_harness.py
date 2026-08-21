"""The failure-control harness must be able to fail.

`scripts/run_local_failure_exercises.py` previously emitted six scenarios with
a hardcoded ``"passed": True`` and executed nothing, while
`docs/public-local-evaluation-report.md` published "6/6 scenarios passed" from
its output. Replacing that with a harness that *runs* the proving tests is only
an improvement if the new harness can actually report failure — otherwise it is
the same theatre with more machinery.

So these tests attack the harness itself: can it detect a renamed proving test,
does it refuse a scenario that names none, and does its output parser fail
closed on input it does not understand.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_harness():
    """Import the script by path — `scripts/` is not an importable package."""
    path = ROOT / "scripts" / "run_local_failure_exercises.py"
    spec = importlib.util.spec_from_file_location("_failure_exercises", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_failure_exercises"] = module
    spec.loader.exec_module(module)
    return module


harness = _load_harness()


class TestEveryScenarioNamesItsProof:
    def test_no_scenario_may_be_claim_only(self):
        """The regression guard for the original defect.

        A scenario with no `node_ids` cannot fail — it would report whatever
        the author asserted. This makes re-adding one a test failure rather
        than a silent return to unverified claims.
        """
        for scenario in harness.SCENARIOS:
            assert scenario.node_ids, (
                f"scenario {scenario.id!r} names no proving test, so its result "
                f"would be an assertion rather than evidence"
            )

    def test_node_ids_are_fully_qualified(self):
        # A bare file path would pass a whole module, silently widening what
        # the scenario claims to prove.
        for scenario in harness.SCENARIOS:
            for node_id in scenario.node_ids:
                assert "::" in node_id, f"{scenario.id}: {node_id} is not a node id"
                assert node_id.startswith("tests/"), f"{scenario.id}: {node_id}"

    def test_scenario_ids_are_unique(self):
        ids = [scenario.id for scenario in harness.SCENARIOS]
        assert len(ids) == len(set(ids))

    def test_the_matrix_is_not_empty(self):
        assert len(harness.SCENARIOS) >= 6


class TestOutputParserFailsClosed:
    @pytest.mark.parametrize("stdout, expected", [
        ("===== 5 passed in 1.20s =====", 5),
        ("===== 3 passed, 2 failed in 1.20s =====", 5),
        ("===== 1 failed in 0.10s =====", 1),
        ("===== 2 passed, 1 error in 0.30s =====", 3),
        ("===== 10 passed, 7 skipped in 4.00s =====", 10),
    ])
    def test_summary_lines_are_parsed(self, stdout, expected):
        assert harness._collected_count(stdout) == expected

    @pytest.mark.parametrize("stdout", [
        "",
        "no tests ran in 1.79s",
        "ERROR: not found: tests/unit/test_x.py::TestY::test_z",
        "some completely unexpected output format",
        "===== passed in 1.20s =====",   # no number to parse
    ])
    def test_unparseable_output_counts_as_zero(self, stdout):
        # Zero is what makes a scenario fail, so an unrecognised pytest output
        # format degrades to "not proven" rather than "proven".
        assert harness._collected_count(stdout) == 0


class TestHarnessDetectsAMissingProof:
    def test_a_renamed_proving_test_fails_the_scenario(self):
        """The failure this harness exists to catch.

        pytest exits 4 when a node id cannot be resolved, which is exactly what
        a renamed or deleted proving test looks like. If this ever passes, a
        control could be deleted while the report kept claiming it.
        """
        phantom = harness.Scenario(
            id="phantom-control",
            expected="denied",
            claim="a control whose proving test no longer exists",
            node_ids=(
                "tests/unit/test_snapshot_integrity.py::TestNonexistent::test_renamed_away",
            ),
        )
        result = harness._run_scenario(phantom)

        assert result["passed"] is False
        assert result["pytest_exit_code"] != 0
        assert result["tests_collected"] == 0
        assert "missing or renamed" in result["failure_reason"]
        # The tail is what an operator reads to find out why.
        assert result["output_tail"]

    def test_a_real_control_passes(self):
        # The positive control: without this, the test above would also pass
        # against a harness that failed everything unconditionally.
        genuine = harness.Scenario(
            id="genuine-control",
            expected="digest_match",
            claim="a mutated snapshot fails integrity verification",
            node_ids=(
                "tests/unit/test_snapshot_integrity.py::TestVerifierCanActuallySayNo"
                "::test_it_is_not_a_constant_true",
            ),
        )
        result = harness._run_scenario(genuine)

        assert result["passed"] is True
        assert result["pytest_exit_code"] == 0
        assert result["tests_collected"] >= 1
        assert "failure_reason" not in result

    def test_a_parametrised_proof_is_not_penalised(self):
        # One node id can legitimately expand to many cases; requiring an exact
        # count would fail a scenario for being better covered than declared.
        parametrised = harness.Scenario(
            id="parametrised-control",
            expected="denied",
            claim="every mutated field fails verification",
            node_ids=(
                "tests/unit/test_snapshot_integrity.py::TestTamperingIsDetected"
                "::test_a_mutated_field_fails_verification",
            ),
        )
        result = harness._run_scenario(parametrised)

        assert result["passed"] is True
        assert result["tests_collected"] > result["tests_expected_minimum"]


class TestReportIsAuditable:
    def test_each_result_carries_the_node_ids_that_produced_it(self):
        result = harness._run_scenario(harness.SCENARIOS[0])
        # Without this a reader cannot rerun what produced the verdict, which
        # is the difference between evidence and a claim.
        assert result["proving_tests"] == list(harness.SCENARIOS[0].node_ids)

    def test_schema_version_was_bumped_away_from_the_unverified_one(self):
        # v1 reports are the hardcoded-True ones; a consumer must be able to
        # tell them apart from executed results.
        import inspect

        source = inspect.getsource(harness)
        assert "local-failure-exercises/v2" in source
        assert "local-failure-exercises/v1" not in source
