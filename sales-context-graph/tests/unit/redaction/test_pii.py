"""Phase 6 (docs/evaluation.md's PII item) — src/redaction/pii.py."""

from __future__ import annotations

from src.redaction.pii import redact_pii


def test_email_is_redacted():
    text = "reach me at jane.doe@example.com for details"
    result = redact_pii(text)
    assert "jane.doe@example.com" not in result
    assert "[EMAIL_REDACTED]" in result


def test_phone_number_is_redacted():
    for phone in ["555-123-4567", "(555) 123-4567", "555.123.4567", "+1 555 123 4567"]:
        result = redact_pii(f"call me at {phone} tomorrow")
        assert phone not in result
        assert "[PHONE_REDACTED]" in result


def test_ssn_is_redacted():
    text = "my ssn is 123-45-6789 for the background check"
    result = redact_pii(text)
    assert "123-45-6789" not in result
    assert "[SSN_REDACTED]" in result


def test_credit_card_is_redacted():
    text = "card number 4111 1111 1111 1111 expires next year"
    result = redact_pii(text)
    assert "4111 1111 1111 1111" not in result
    assert "[CARD_REDACTED]" in result


def test_non_pii_text_passes_through_unchanged():
    text = "we discussed pricing and the renewal timeline for the deal"
    assert redact_pii(text) == text


def test_empty_and_falsy_text_returned_as_is():
    assert redact_pii("") == ""
    assert redact_pii(None) is None


def test_multiple_pii_items_in_one_string_all_redacted():
    text = "email jane@example.com or call 555-123-4567, ssn 123-45-6789"
    result = redact_pii(text)
    assert "[EMAIL_REDACTED]" in result
    assert "[PHONE_REDACTED]" in result
    assert "[SSN_REDACTED]" in result
    assert "jane@example.com" not in result
    assert "555-123-4567" not in result
    assert "123-45-6789" not in result


def test_ssn_shape_is_not_mistaken_for_a_phone_number():
    """3-2-4 (SSN) and 3-3-4 (phone) are different shapes -- confirms the
    two patterns don't cross-match each other's territory."""
    result = redact_pii("123-45-6789")
    assert result == "[SSN_REDACTED]"  # not [PHONE_REDACTED], not left partially matched
