from graphrag.graph.ontology_migration import plan_migration
from graphrag.observability.cost_attribution import CostEvent, aggregate_costs
from graphrag.ops.exercises import recovery_check, security_matrix
from graphrag.ops.production_exercises import (
    run_backup_recovery_exercise, run_load_exercise, run_security_exercise,
)
from graphrag.retrieval.query_planner import classify_query, retrieval_plan


def test_query_planner_routes_classes_and_fallbacks():
    assert classify_query("Which documents conflict?") == "contradiction"
    assert retrieval_plan("Compare the chain across steps")["fallback"] == "agentic"


def test_migration_report_requires_mapping_for_removals():
    report = plan_migration({"classes": {"Old": {}}, "properties": {}},
                            {"classes": {"New": {}}, "properties": {},
                             "migration_map": {"Old": "New"}})
    assert report.compatible is True
    assert report.renamed == [("Old", "New")]


def test_cost_attribution_and_exercise_helpers():
    events = [CostEvent("acme", "map", "groq", "m", 0.1),
              CostEvent("acme", "map", "groq", "m", 0.2)]
    assert aggregate_costs(events)[0]["cost_usd"] == 0.30000000000000004
    assert security_matrix([{"name": "cross", "expected_tenant": "a", "observed_tenant": "b"}])["failed"] == 1
    assert recovery_check("abc", "abc")["match"] is True


async def test_p3_load_security_and_backup_exercises():
    async def operation(case):
        if case.get("fail"):
            raise RuntimeError("synthetic failure")

    load = await run_load_exercise(operation, [{"tenant": "a"}, {"tenant": "b", "fail": True}], 2)
    assert load["total"] == 2 and load["failed"] == 1
    security = run_security_exercise([
        {"name": "restricted", "expected_tenant": "a", "observed_tenant": "a",
         "restricted": True, "allowed": False},
        {"name": "destructive", "expected_tenant": "a", "observed_tenant": "a",
         "destructive": True, "approval_required": False},
    ])
    assert security["failed"] == 1
    recovery = await run_backup_recovery_exercise(lambda: _value("digest"), lambda _: _value("digest"))
    assert recovery["match"] is True


async def _value(value):
    return value
