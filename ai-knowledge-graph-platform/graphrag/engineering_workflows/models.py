"""Typed contracts for repository-local engineering workflows."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WorkflowStatus(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


class TaskKind(StrEnum):
    COMMAND = "command"
    SKILL = "skill"
    AGENT = "agent"


class WorkflowTask(BaseModel):
    """One deterministic or extension-backed step in a workflow."""

    id: str = Field(min_length=1, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
    title: str = Field(min_length=1)
    kind: TaskKind = TaskKind.SKILL
    depends_on: list[str] = Field(default_factory=list)
    skill: str | None = None
    agent: str | None = None
    command: list[str] | None = None
    approval_required: bool = False
    timeout_seconds: float = Field(default=900.0, gt=0, le=86_400)
    continue_on_failure: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_executor(self) -> "WorkflowTask":
        configured = sum(value is not None for value in (self.skill, self.agent, self.command))
        if configured != 1:
            raise ValueError("a task must configure exactly one of skill, agent, or command")
        if self.kind == TaskKind.SKILL and self.skill is None:
            raise ValueError("skill tasks require skill")
        if self.kind == TaskKind.AGENT and self.agent is None:
            raise ValueError("agent tasks require agent")
        if self.kind == TaskKind.COMMAND and self.command is None:
            raise ValueError("command tasks require command")
        if self.command is not None and not all(isinstance(arg, str) and arg for arg in self.command):
            raise ValueError("command arguments must be non-empty strings")
        return self


class WorkflowSpec(BaseModel):
    """A validated specification-to-implementation plan."""

    schema_version: str = "1.0"
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    tasks: list[WorkflowTask] = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_dag(self) -> "WorkflowSpec":
        task_ids = [task.id for task in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("workflow task IDs must be unique")
        known = set(task_ids)
        for task in self.tasks:
            missing = set(task.depends_on) - known
            if missing:
                raise ValueError(f"task {task.id!r} depends on unknown tasks: {sorted(missing)}")
        pending = {task.id: set(task.depends_on) for task in self.tasks}
        resolved: set[str] = set()
        while pending:
            ready = [task_id for task_id, deps in pending.items() if deps <= resolved]
            if not ready:
                raise ValueError("workflow task dependencies must form an acyclic graph")
            for task_id in ready:
                pending.pop(task_id)
                resolved.add(task_id)
        return self


class TaskResult(BaseModel):
    task_id: str
    status: TaskStatus
    started_at: datetime
    finished_at: datetime | None = None
    exit_code: int | None = None
    output: str = ""
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowRun(BaseModel):
    run_id: str
    workflow_id: str
    status: WorkflowStatus = WorkflowStatus.PLANNED
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    approval_granted: bool = False
    task_results: dict[str, TaskResult] = Field(default_factory=dict)
    error: str | None = None

