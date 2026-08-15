"""Lifecycle hooks for workflow observability and policy enforcement."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Awaitable, Callable


class HookEvent(StrEnum):
    WORKFLOW_STARTED = "workflow_started"
    TASK_STARTED = "task_started"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    APPROVAL_REQUIRED = "approval_required"
    WORKFLOW_COMPLETED = "workflow_completed"
    WORKFLOW_FAILED = "workflow_failed"


@dataclass(frozen=True)
class HookContext:
    event: HookEvent
    run_id: str
    workflow_id: str
    root: Path
    task_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


Hook = Callable[[HookContext], Awaitable[None]]


class HookRegistry:
    def __init__(self) -> None:
        self._hooks: dict[HookEvent, list[Hook]] = {event: [] for event in HookEvent}

    def register(self, event: HookEvent, hook: Hook) -> None:
        self._hooks[event].append(hook)

    async def emit(self, context: HookContext) -> None:
        for hook in self._hooks[context.event]:
            await hook(context)


__all__ = ["HookContext", "HookEvent", "HookRegistry"]
