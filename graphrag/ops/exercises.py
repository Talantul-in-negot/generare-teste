"""Deterministic checks used by load, security, and recovery exercises.

This is the pure-logic layer: no I/O, no awaiting, fully unit-testable.
``graphrag.ops.production_exercises`` is the orchestration layer that drives
real backups, real tool calls and real load, and delegates its decisions here.

The two modules previously carried near-duplicate implementations of the same
security matrix — ``production_exercises.run_security_exercise`` additionally
checked the destructive/approval gate and used a different key name for the
failing case, so the "same" check gave two different answers depending on
which entry point you called. There is now one implementation.
"""

from __future__ import annotations


def security_matrix(cases: list[dict]) -> dict:
    """Evaluate tenant-isolation and tool-authorisation cases.

    Each case may declare:
      expected_tenant / observed_tenant — must match, else tenant_isolation
      restricted + allowed              — a restricted tool that ran anyway
      destructive + approval_required   — a destructive tool with no gate
    """
    failures = []
    for case in cases:
        name = case.get("name", "unknown")
        if case.get("expected_tenant") != case.get("observed_tenant"):
            failures.append({"name": name, "case": name, "reason": "tenant_isolation"})
        if case.get("restricted") and case.get("allowed"):
            failures.append({"name": name, "case": name, "reason": "restricted_tool_allowed"})
        if case.get("destructive") and not case.get("approval_required"):
            failures.append({"name": name, "case": name, "reason": "missing_approval_gate"})
    return {
        "total":    len(cases),
        "passed":   len(cases) - len(failures),
        "failed":   len(failures),
        "failures": failures,
    }


def recovery_check(backup_hash: str, restored_hash: str) -> dict:
    """Compare a backup digest against the digest of the restored graph."""
    return {
        "backup_hash":   backup_hash,
        "restored_hash": restored_hash,
        "match":         bool(backup_hash) and backup_hash == restored_hash,
    }
