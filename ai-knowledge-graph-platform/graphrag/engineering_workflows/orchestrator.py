"""Persistent, approval-aware workflow execution."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from graphrag.engineering_workflows.hooks import HookContext, HookEvent, HookRegistry
from graphrag.engineering_workflows.models import (
    TaskResult,
    TaskStatus,
    WorkflowRun,
    WorkflowSpec,
    WorkflowStatus,
    WorkflowTask,
)
from graphrag.engineering_workflows.registry import SkillRegistry, default_skill_registry


class WorkflowOrchestrator:
    """Run a validated workflow with durable state and explicit approvals."""

    def __init__(
        self,
        root: Path,
        *,
        state_dir: Path | None = None,
        skills: SkillRegistry | None = None,
        hooks: HookRegistry | None = None,
    ) -> None:
        self.root = root.resolve()
        self.state_dir = (state_dir or self.root / ".workflows" / "runs").resolve()
        self.skills = skills or default_skill_registry()
        self.hooks = hooks or HookRegistry()

    @staticmethod
    def load_spec(path: Path) -> WorkflowSpec:
        with path.open("r", encoding="utf-8") as stream:
            raw = yaml.safe_load(stream) or {}
        return WorkflowSpec.model_validate(raw)

    def _state_path(self, run_id: str) -> Path:
        if Path(run_id).name != run_id or not run_id.strip():
            raise ValueError("run_id must be a simple filename-safe identifier")
        return self.state_dir / f"{run_id}.json"

    def _persist(self, run: WorkflowRun) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        path = self._state_path(run.run_id)
        temp = path.with_suffix(".json.tmp")
        temp.write_text(run.model_dump_json(indent=2), encoding="utf-8")
        os.replace(temp, path)

    def load_run(self, run_id: str) -> WorkflowRun:
        return WorkflowRun.model_validate_json(self._state_path(run_id).read_text(encoding="utf-8"))

    async def run(
        self,
        spec: WorkflowSpec,
        *,
        run_id: str | None = None,
        approval_granted: bool = False,
        resume: bool = False,
    ) -> WorkflowRun:
        run = self.load_run(run_id) if resume and run_id else WorkflowRun(
            run_id=run_id or f"{spec.id}-{uuid.uuid4().hex[:12]}", workflow_id=spec.id,
        )
        if run.workflow_id != spec.id:
            raise ValueError("run state belongs to a different workflow")
        run.approval_granted = run.approval_granted or approval_granted
        run.status = WorkflowStatus.RUNNING
        run.updated_at = _utc_now()
        self._persist(run)
        await self.hooks.emit(HookContext(HookEvent.WORKFLOW_STARTED, run.run_id, spec.id, self.root))

        try:
            for task in _ordered_tasks(spec.tasks):
                previous = run.task_results.get(task.id)
                if previous and previous.status == TaskStatus.COMPLETED:
                    continue
                if any(run.task_results.get(dep, TaskResult(
                    task_id=dep, status=TaskStatus.BLOCKED,
                    started_at=_utc_now(),
                )).status != TaskStatus.COMPLETED for dep in task.depends_on):
                    result = TaskResult(task_id=task.id, status=TaskStatus.BLOCKED,
                                        started_at=_utc_now(), finished_at=_utc_now(),
                                        error="dependency did not complete")
                    run.task_results[task.id] = result
                    run.status = WorkflowStatus.FAILED
                    run.error = result.error
                    self._persist(run)
                    break
                if _needs_approval(task) and not run.approval_granted:
                    run.status = WorkflowStatus.WAITING_APPROVAL
                    run.updated_at = _utc_now()
                    self._persist(run)
                    await self.hooks.emit(HookContext(
                        HookEvent.APPROVAL_REQUIRED, run.run_id, spec.id, self.root,
                        task_id=task.id, payload={"title": task.title},
                    ))
                    return run
                result = await self._execute_task(run, spec, task)
                run.task_results[task.id] = result
                run.updated_at = _utc_now()
                self._persist(run)
                if result.status == TaskStatus.FAILED and not task.continue_on_failure:
                    run.status = WorkflowStatus.FAILED
                    run.error = result.error or f"task failed: {task.id}"
                    self._persist(run)
                    await self.hooks.emit(HookContext(
                        HookEvent.WORKFLOW_FAILED, run.run_id, spec.id, self.root,
                        task_id=task.id, payload={"error": run.error},
                    ))
                    return run
            else:
                run.status = WorkflowStatus.COMPLETED
                run.updated_at = _utc_now()
                self._persist(run)
                await self.hooks.emit(HookContext(HookEvent.WORKFLOW_COMPLETED, run.run_id, spec.id, self.root))
                return run
        except Exception as exc:
            run.status = WorkflowStatus.FAILED
            run.error = str(exc)
            run.updated_at = _utc_now()
            self._persist(run)
            await self.hooks.emit(HookContext(
                HookEvent.WORKFLOW_FAILED, run.run_id, spec.id, self.root,
                payload={"error": str(exc)},
            ))
        return run

    async def _execute_task(self, run: WorkflowRun, spec: WorkflowSpec, task: WorkflowTask) -> TaskResult:
        started = _utc_now()
        await self.hooks.emit(HookContext(
            HookEvent.TASK_STARTED, run.run_id, spec.id, self.root, task_id=task.id,
        ))
        try:
            if task.command is not None:
                command = task.command
            elif task.skill is not None:
                definition = self.skills.get(task.skill)
                if definition.command_builder is None:
                    payload = await definition.handler(task, self.root)  # type: ignore[misc]
                    output = _format_output(payload)
                    result = TaskResult(task_id=task.id, status=TaskStatus.COMPLETED,
                                        started_at=started, finished_at=_utc_now(), output=output)
                    await self.hooks.emit(HookContext(
                        HookEvent.TASK_COMPLETED, run.run_id, spec.id, self.root,
                        task_id=task.id, payload={"output": output},
                    ))
                    return result
                command = definition.command_builder(self.root, task)
            else:
                handler = self.skills.get_agent(task.agent or "")
                payload = await handler(task, self.root)
                output = _format_output(payload)
                result = TaskResult(task_id=task.id, status=TaskStatus.COMPLETED,
                                    started_at=started, finished_at=_utc_now(), output=output)
                await self.hooks.emit(HookContext(
                    HookEvent.TASK_COMPLETED, run.run_id, spec.id, self.root,
                    task_id=task.id, payload={"output": output},
                ))
                return result
            _validate_command(command, self.root)
            process = await asyncio.create_subprocess_exec(
                *command, cwd=self.root, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=task.timeout_seconds)
            output = stdout.decode("utf-8", errors="replace")[-100_000:]
            status = TaskStatus.COMPLETED if process.returncode == 0 else TaskStatus.FAILED
            result = TaskResult(task_id=task.id, status=status, started_at=started,
                                finished_at=_utc_now(), exit_code=process.returncode,
                                output=output, error=None if status == TaskStatus.COMPLETED else output)
            await self.hooks.emit(HookContext(
                HookEvent.TASK_COMPLETED if status == TaskStatus.COMPLETED else HookEvent.TASK_FAILED,
                run.run_id, spec.id, self.root, task_id=task.id,
                payload={"exit_code": process.returncode, "output": output},
            ))
            return result
        except asyncio.TimeoutError:
            result = TaskResult(task_id=task.id, status=TaskStatus.FAILED,
                                started_at=started, finished_at=_utc_now(),
                                error=f"task exceeded {task.timeout_seconds:g}s timeout")
            await self.hooks.emit(HookContext(HookEvent.TASK_FAILED, run.run_id, spec.id, self.root,
                                              task_id=task.id, payload={"error": result.error}))
            return result
        except Exception as exc:
            result = TaskResult(task_id=task.id, status=TaskStatus.FAILED,
                                started_at=started, finished_at=_utc_now(), error=str(exc))
            await self.hooks.emit(HookContext(HookEvent.TASK_FAILED, run.run_id, spec.id, self.root,
                                              task_id=task.id, payload={"error": str(exc)}))
            return result


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ordered_tasks(tasks: list[WorkflowTask]) -> list[WorkflowTask]:
    remaining = {task.id: task for task in tasks}
    ordered: list[WorkflowTask] = []
    resolved: set[str] = set()
    while remaining:
        ready = [task for task in remaining.values() if set(task.depends_on) <= resolved]
        if not ready:
            raise ValueError("workflow task dependencies are not executable")
        for task in ready:
            ordered.append(task)
            resolved.add(task.id)
            remaining.pop(task.id)
    return ordered


def _needs_approval(task: WorkflowTask) -> bool:
    if task.approval_required:
        return True
    if task.command:
        lowered = " ".join(task.command).lower()
        return any(token in lowered for token in ("git commit", "git push", "git reset", "git clean"))
    return False


def _validate_command(command: list[str], root: Path) -> None:
    if not command or any(not isinstance(arg, str) or not arg for arg in command):
        raise ValueError("command must be a non-empty argv list")
    if any(arg in {"&&", "||", ";", "|", ">", "<"} for arg in command):
        raise ValueError("shell operators are not allowed in workflow commands")
    for arg in command[1:]:
        if arg.startswith("-") or not any(char in arg for char in ("/", "\\", ".")):
            continue
        candidate = Path(arg).resolve() if Path(arg).is_absolute() else (root / arg).resolve()
        if candidate != root and root not in candidate.parents:
            raise ValueError(f"command path escapes workflow root: {arg}")


def _format_output(payload: dict[str, Any] | str | None) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, sort_keys=True, default=str)


__all__ = ["WorkflowOrchestrator"]
