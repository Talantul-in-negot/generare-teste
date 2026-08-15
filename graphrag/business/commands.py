"""Command envelope and receipt for agent/human-driven business-object writes.

`CommandEnvelope` is the one shape every mutating capability call takes,
whether it arrives over HTTP (`api/routes/business.py`) or, later, over MCP
(`mcp_server/capabilities/workorder_create.py`). `CommandReceipt` is the one
shape every call returns -- outcome is always one of a fixed set, never a
raw exception, so a caller (human or agent) can branch on it without
knowing which capability it called.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class CommandOutcome(StrEnum):
    EXECUTED = "executed"
    DRY_RUN = "dry_run"
    DENIED = "denied"
    APPROVAL_REQUIRED = "approval_required"
    STALE_VERSION = "stale_version"


class CommandEnvelope(BaseModel):
    command_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    capability: str = Field(min_length=1)
    tenant: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    actor_type: str = "human"
    reason_code: str = Field(min_length=1)
    args: dict[str, Any] = Field(default_factory=dict)
    expected_version: int | None = None
    dry_run: bool = False
    approval_id: str | None = None
    correlation_id: str = ""


class CommandReceipt(BaseModel):
    """Immutable record of what a command envelope actually did.

    `receipt_hash` covers every field except itself (same pattern as
    `ContextManifest.compute_integrity_hash` / `_trace_hash` in
    `context_graph`), so a stored receipt can be verified against a
    recomputed hash of its own content.
    """

    id: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    tenant: str = Field(min_length=1)
    command_id: str = Field(min_length=1)
    capability: str = Field(min_length=1)
    outcome: CommandOutcome
    object_id: str | None = None
    object_type: str | None = None
    from_version: int | None = None
    to_version: int | None = None
    from_state: str | None = None
    to_state: str | None = None
    policy_result: str | None = None
    approval_id: str | None = None
    corpus_revision: int | None = None
    denial_reason: str | None = None
    detail: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    receipt_hash: str = ""

    def canonical_content(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"receipt_hash"})

    def compute_receipt_hash(self) -> str:
        payload = json.dumps(self.canonical_content(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def with_receipt_hash(self) -> "CommandReceipt":
        self.receipt_hash = self.compute_receipt_hash()
        return self
