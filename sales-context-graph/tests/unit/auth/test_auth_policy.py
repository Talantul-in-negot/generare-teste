from __future__ import annotations

import pytest

from src.auth.policy import (
    AccessContext,
    AccessDenied,
    can_read_asset,
    require_division,
    require_opportunity,
)


def test_non_admin_cannot_read_unassigned_division() -> None:
    context = AccessContext("ws", "seller", opportunity_ids=frozenset({"opp-1"}))
    assert not can_read_asset(context, division_id=None, is_sensitive=False)


def test_sensitive_asset_requires_compliance_role() -> None:
    context = AccessContext("ws", "seller", division_ids=frozenset({"div-1"}))
    assert not can_read_asset(context, division_id="div-1", is_sensitive=True)
    compliance = AccessContext(
        "ws", "compliance", roles=frozenset({"compliance"}), division_ids=frozenset({"div-1"})
    )
    assert can_read_asset(compliance, division_id="div-1", is_sensitive=True)


def test_scope_requirements_raise_for_out_of_scope_resource() -> None:
    context = AccessContext("ws", "seller", division_ids=frozenset({"div-1"}), opportunity_ids=frozenset({"opp-1"}))
    with pytest.raises(AccessDenied):
        require_division(context, "div-2")
    with pytest.raises(AccessDenied):
        require_opportunity(context, "opp-2")
