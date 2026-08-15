"""Skill and subagent registries for workflow extension points."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from graphrag.engineering_workflows.models import WorkflowTask


CommandBuilder = Callable[[Path, WorkflowTask], list[str]]
AsyncHandler = Callable[[WorkflowTask, Path], Awaitable[dict[str, Any] | str | None]]


@dataclass(frozen=True)
class SkillDefinition:
    name: str
    description: str
    command_builder: CommandBuilder | None = None
    handler: AsyncHandler | None = None


class SkillRegistry:
    """Allowlisted skills; workflows cannot invoke an unknown skill."""

    def __init__(self) -> None:
        self._skills: dict[str, SkillDefinition] = {}
        self._agents: dict[str, AsyncHandler] = {}

    def register(self, definition: SkillDefinition) -> None:
        if definition.name in self._skills:
            raise ValueError(f"skill already registered: {definition.name}")
        if definition.command_builder is None and definition.handler is None:
            raise ValueError("a skill needs a command builder or handler")
        self._skills[definition.name] = definition

    def register_agent(self, name: str, handler: AsyncHandler) -> None:
        if name in self._agents:
            raise ValueError(f"agent already registered: {name}")
        self._agents[name] = handler

    def get(self, name: str) -> SkillDefinition:
        try:
            return self._skills[name]
        except KeyError as exc:
            raise KeyError(f"unknown workflow skill: {name}") from exc

    def get_agent(self, name: str) -> AsyncHandler:
        try:
            return self._agents[name]
        except KeyError as exc:
            raise KeyError(f"unknown workflow agent: {name}") from exc

    def skills(self) -> tuple[SkillDefinition, ...]:
        return tuple(self._skills.values())


def _command(*args: str) -> CommandBuilder:
    return lambda _root, _task: [sys.executable, *args]


def default_skill_registry() -> SkillRegistry:
    """Return the safe built-in engineering skills available offline."""

    registry = SkillRegistry()
    registry.register(SkillDefinition(
        name="lint",
        description="Run Ruff against declared workflow paths.",
        command_builder=lambda root, task: [
            sys.executable, "-m", "ruff", "check",
            *[str((root / path).resolve()) for path in task.metadata.get(
                "paths", ["graphrag/engineering_workflows"],
            )],
        ],
    ))
    registry.register(SkillDefinition(
        name="unit-tests",
        description="Run the unit test suite.",
        command_builder=lambda _root, _task: [sys.executable, "-m", "pytest", "tests/unit", "-q"],
    ))
    registry.register(SkillDefinition(
        name="integration-tests",
        description="Run integration tests.",
        command_builder=lambda _root, _task: [sys.executable, "-m", "pytest", "tests/integration", "-q"],
    ))
    registry.register(SkillDefinition(
        name="full-tests",
        description="Run the complete test suite.",
        command_builder=lambda _root, _task: [sys.executable, "-m", "pytest", "-q"],
    ))
    return registry


__all__ = ["SkillDefinition", "SkillRegistry", "default_skill_registry"]
