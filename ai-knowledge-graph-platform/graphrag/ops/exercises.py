"""Deterministic checks used by load, security, and recovery exercises."""

from __future__ import annotations


def security_matrix(cases: list[dict]) -> dict:
    failures = []
    for case in cases:
        if case.get("expected_tenant") != case.get("observed_tenant"):
            failures.append({"case": case.get("name", "unknown"), "reason": "tenant_isolation"})
        if case.get("restricted") and case.get("allowed"):
            failures.append({"case": case.get("name", "unknown"), "reason": "restricted_tool"})
    return {"total": len(cases), "failed": len(failures), "passed": len(cases) - len(failures),
            "failures": failures}


def recovery_check(backup_hash: str, restored_hash: str) -> dict:
    return {"backup_hash": backup_hash, "restored_hash": restored_hash,
            "match": bool(backup_hash) and backup_hash == restored_hash}
