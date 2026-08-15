"""Abstract agent base.

Historical note
---------------
This class used to build a ``google.adk.agents.Agent`` in ``__init__`` and
register per-agent tool lists through an abstract ``_tools()`` hook. That
scaffolding was dead at runtime: ``google-adk`` was never declared in any
requirements file, so the import always failed, ``_build_agent()`` always
returned ``None``, ``_tools()`` was never called, and the resulting
``self._agent`` attribute was never read anywhere in the codebase.

It has been removed rather than left as aspirational scaffolding. Tool
dispatch is a real, tested code path: see :class:`graphrag.agents.tool_policy.ToolPolicy`,
which is what actually gates tool execution (allowlist, scopes, argument
validation, timeout, audit log) and is exposed over HTTP at ``POST /agent/tool``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import structlog

log = structlog.get_logger(__name__)


class BaseGraphRAGAgent(ABC):
    """Abstract agent base. Subclasses implement `run()`."""

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def _model(self) -> str:
        """Return the model identifier (e.g. cfg.groq_model) used for provenance stamping.

        Actual API calls go through ``graphrag.core.llm_client.get_llm()``.
        """

    @abstractmethod
    def _instruction(self) -> str:
        """Return the agent system instruction."""

    @abstractmethod
    async def run(self, **kwargs) -> Any:
        """Execute the agent's primary task."""
