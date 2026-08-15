from __future__ import annotations

import sys
from pathlib import Path

import pytest

from graphrag.engineering_workflows import WorkflowOrchestrator
from graphrag.engineering_workflows.models import TaskKind, WorkflowSpec, WorkflowStatus, WorkflowTask
from graphrag.engineering_workflows.registry import SkillDefinition, SkillRegistry


def _spec(*tasks: WorkflowTask) -> WorkflowSpec:
    return WorkflowSpec(
        id="test-workflow", title="Test workflow", objective="Exercise the runner", tasks=list(tasks),
    )


def test_workflow_rejects_cycles_and_unknown_dependencies() -> None:
    with pytest.raises(ValueError, match="unknown tasks"):
        _spec(WorkflowTask(
            id="a", title="A", skill="unit-tests", depends_on=["missing"],
        ))
    with pytest.raises(ValueError, match="acyclic"):
        _spec(
            WorkflowTask(id="a", title="A", skill="unit-tests", depends_on=["b"]),
            WorkflowTask(id="b", title="B", skill="unit-tests", depends_on=["a"]),
        )


@pytest.mark.asyncio
async def test_runner_persists_results_and_emits_hooks(tmp_path: Path) -> None:
    events: list[str] = []
    registry = SkillRegistry()
    registry.register(SkillDefinition(
        name="test-skill",
        description="A deterministic test skill",
        command_builder=lambda _root, _task: [sys.executable, "-c", "print('ok')"],
    ))
    orchestrator = WorkflowOrchestrator(tmp_path, skills=registry)
    from graphrag.engineering_workflows.hooks import HookEvent

    async def record(context):
        events.append(context.event.value)

    orchestrator.hooks.register(HookEvent.TASK_COMPLETED, record)
    result = await orchestrator.run(_spec(WorkflowTask(id="a", title="A", skill="test-skill")), run_id="run-1")

    assert result.status == WorkflowStatus.COMPLETED
    assert result.task_results["a"].output.strip() == "ok"
    assert events == ["task_completed"]
    assert orchestrator.load_run("run-1").status == WorkflowStatus.COMPLETED


@pytest.mark.asyncio
async def test_approval_gate_pauses_and_resume_skips_completed_tasks(tmp_path: Path) -> None:
    calls = 0
    registry = SkillRegistry()

    async def handler(_task, _root):
        nonlocal calls
        calls += 1
        return "done"

    registry.register(SkillDefinition(name="handler", description="test", handler=handler))
    orchestrator = WorkflowOrchestrator(tmp_path, skills=registry)
    spec = _spec(
        WorkflowTask(id="build", title="Build", skill="handler"),
        WorkflowTask(
            id="publish", title="Publish", kind=TaskKind.COMMAND,
            command=[sys.executable, "-c", "print('published')"],
            depends_on=["build"], approval_required=True,
        ),
    )

    paused = await orchestrator.run(spec, run_id="run-approval")
    assert paused.status == WorkflowStatus.WAITING_APPROVAL
    assert calls == 1
    resumed = await orchestrator.run(spec, run_id="run-approval", resume=True, approval_granted=True)
    assert resumed.status == WorkflowStatus.COMPLETED
    assert calls == 1


def test_command_workflow_requires_approval_for_git_mutation() -> None:
    task = WorkflowTask(id="push", title="Push", kind=TaskKind.COMMAND,
                        command=["git", "push", "origin", "main"])
    from graphrag.engineering_workflows.orchestrator import _needs_approval

    assert _needs_approval(task) is True


@pytest.mark.asyncio
async def test_runner_rejects_command_paths_outside_repository(tmp_path: Path) -> None:
    registry = SkillRegistry()
    orchestrator = WorkflowOrchestrator(tmp_path, skills=registry)
    spec = _spec(WorkflowTask(
        id="escape", title="Escape", kind=TaskKind.COMMAND,
        command=[sys.executable, "..", "outside.py"],
    ))

    result = await orchestrator.run(spec, run_id="run-escape")

    assert result.status == WorkflowStatus.FAILED
    assert "escapes workflow root" in (result.task_results["escape"].error or "")
