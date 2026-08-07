"""Phase 6 (docs/evaluation.md's PII item) — src/core/logging.py's
_redact_pii_processor, the mechanical backstop behind
docs/security-and-tenancy.md's "no transcript text or email in logs" claim.
"""

from __future__ import annotations

import pytest

from src.core.config import get_settings
from src.core.logging import _redact_pii_processor


@pytest.fixture(autouse=True)
def _reset_settings():
    yield
    get_settings.cache_clear()


def test_redacts_pii_in_every_string_field():
    event_dict = {
        "event": "some.event",
        "excerpt": "reach me at jane@example.com",
        "phone": "call 555-123-4567 now",
        "count": 3,  # non-string fields pass through untouched
    }
    result = _redact_pii_processor(None, "info", event_dict)
    assert "jane@example.com" not in result["excerpt"]
    assert "[EMAIL_REDACTED]" in result["excerpt"]
    assert "[PHONE_REDACTED]" in result["phone"]
    assert result["count"] == 3


def test_fields_with_no_pii_are_unchanged():
    event_dict = {"event": "ingestion.completed", "ingestion_id": "job-123", "workspace_id": "ws-1"}
    result = _redact_pii_processor(None, "info", dict(event_dict))
    assert result == event_dict


def test_disabled_redaction_is_a_no_op(monkeypatch):
    monkeypatch.setenv("PII_REDACTION_ENABLED", "false")
    get_settings.cache_clear()
    event_dict = {"event": "some.event", "excerpt": "reach me at jane@example.com"}
    result = _redact_pii_processor(None, "info", dict(event_dict))
    assert result == event_dict  # unchanged -- redaction is off
