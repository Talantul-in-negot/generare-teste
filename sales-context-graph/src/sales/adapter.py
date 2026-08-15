"""Deterministic local CRM adapter used for offline demos and CI.

The adapter intentionally has no network or external-CRM side effects. Its
interface is small enough for a Salesforce/Dynamics implementation to replace
later while preserving command safety invariants.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Protocol

from src.domain.sales import SalesCompensationAction, SalesCRMWrite


class CRMAdapter(Protocol):
    def preview(self, command: SalesCRMWrite) -> dict: ...
    def execute(self, command: SalesCRMWrite) -> "CRMReceipt": ...
    def compensate(self, action: SalesCompensationAction) -> "CRMReceipt": ...


class CRMCommandError(RuntimeError):
    """A rejected command that must not mutate CRM state."""


@dataclass(frozen=True)
class CRMReceipt:
    command_id: str
    workspace_id: str
    object_id: str
    outcome: str
    version: int
    diff: dict
    correlation_id: str
    recorded_at: str
    receipt_hash: str
    compensation: SalesCompensationAction | None = None


class LocalCRMEmulator:
    """In-memory, tenant-isolated CRM with deterministic receipt semantics."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], dict] = {}
        self._commands: dict[tuple[str, str], CRMReceipt] = {}
        self.audit_events: list[dict] = []

    def seed(self, *, workspace_id: str, object_id: str, values: dict, version: int = 1) -> None:
        self._records[(workspace_id, object_id)] = {"version": version, **copy.deepcopy(values)}

    def _record(self, command: SalesCRMWrite) -> dict:
        try:
            return self._records[(command.workspace_id, command.object_id)]
        except KeyError as exc:
            raise CRMCommandError("CRM object not found in the command workspace") from exc

    def preview(self, command: SalesCRMWrite) -> dict:
        current = self._record(command)
        if current["version"] != command.expected_version:
            raise CRMCommandError("stale CRM version")
        before = {key: current.get(key) for key in command.patch}
        after = {key: value for key, value in command.patch.items()}
        return {"workspace_id": command.workspace_id, "object_id": command.object_id,
                "expected_version": command.expected_version, "before": before, "after": after}

    @staticmethod
    def _hash(payload: dict) -> str:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return sha256(canonical.encode("utf-8")).hexdigest()

    def execute(self, command: SalesCRMWrite) -> CRMReceipt:
        key = (command.workspace_id, command.command_id)
        existing = self._commands.get(key)
        if existing:
            if existing.diff.get("after") != command.patch:
                raise CRMCommandError("command_id was already used with a different payload")
            self.audit_events.append({"event": "crm.command.replay", "command_id": command.command_id})
            return existing
        if command.dry_run:
            diff = self.preview(command)
            return self._receipt(command, "PREVIEW", command.expected_version, diff, None)
        diff = self.preview(command)
        if not command.approved and {"stage", "forecast_category", "close_date", "discount"}.intersection(command.patch):
            raise CRMCommandError("approval required for high-risk CRM command")
        record = self._record(command)
        previous = {key: record.get(key) for key in command.patch}
        record.update(command.patch)
        record["version"] += 1
        compensation = SalesCompensationAction(
            compensation_id=f"compensate-{command.command_id}", workspace_id=command.workspace_id,
            original_command_id=command.command_id, object_id=command.object_id,
            restore_patch=previous,
        )
        receipt = self._receipt(command, "EXECUTED", record["version"], diff, compensation)
        self._commands[key] = receipt
        self.audit_events.append({"event": "crm.command.executed", "command_id": command.command_id,
                                  "workspace_id": command.workspace_id, "correlation_id": command.correlation_id})
        return receipt

    def compensate(self, action: SalesCompensationAction) -> CRMReceipt:
        command = SalesCRMWrite(
            command_id=action.compensation_id, workspace_id=action.workspace_id, actor_id="compensation",
            capability="sales.crm.compensate", object_id=action.object_id, patch=action.restore_patch,
            expected_version=self._records[(action.workspace_id, action.object_id)]["version"],
            approved=True, correlation_id=action.compensation_id,
        )
        receipt = self.execute(command)
        self.audit_events.append({"event": "crm.command.compensated", "original_command_id": action.original_command_id})
        return receipt

    def _receipt(self, command: SalesCRMWrite, outcome: str, version: int, diff: dict,
                 compensation: SalesCompensationAction | None) -> CRMReceipt:
        payload = {"command_id": command.command_id, "workspace_id": command.workspace_id,
                   "object_id": command.object_id, "outcome": outcome, "version": version, "diff": diff,
                   "correlation_id": command.correlation_id, "recorded_at": datetime.now(timezone.utc).isoformat()}
        return CRMReceipt(**payload, receipt_hash=self._hash(payload), compensation=compensation)
