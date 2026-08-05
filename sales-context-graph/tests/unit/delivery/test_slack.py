"""Increment 17 — build_slack_blocks is pure; no network in this file."""

from __future__ import annotations

from datetime import datetime, timezone

from src.delivery.slack import build_slack_blocks
from src.signals.models import Signal, SignalType
from src.usecases.digest import Digest

_NOW = datetime(2026, 8, 5, tzinfo=timezone.utc)


def test_no_signals_produces_a_reassuring_message():
    digest = Digest(workspace_id="ws-1", seller_id=None, generated_at=_NOW, opportunity_count=3, signals=[])
    payload = build_slack_blocks(digest)
    text = payload["blocks"][1]["text"]["text"]
    assert "No signals" in text


def test_signals_are_grouped_by_opportunity():
    signals = [
        Signal(signal_type=SignalType.SINGLE_THREADED_DEAL, severity="warning", opportunity_id="opp-1",
               headline="only one buyer contact", detected_at=_NOW),
        Signal(signal_type=SignalType.STALLED_DEAL, severity="warning", opportunity_id="opp-1",
               headline="no movement in 30 days", detected_at=_NOW),
        Signal(signal_type=SignalType.SHARED_CONTENT_NEVER_OPENED, severity="info", opportunity_id="opp-2",
               headline="unopened deck", detected_at=_NOW),
    ]
    digest = Digest(workspace_id="ws-1", seller_id="s1", generated_at=_NOW, opportunity_count=2, signals=signals)
    payload = build_slack_blocks(digest)

    section_texts = [b["text"]["text"] for b in payload["blocks"] if b["type"] == "section"]
    opp1_section = next(t for t in section_texts if "opp-1" in t)
    assert "only one buyer contact" in opp1_section
    assert "no movement in 30 days" in opp1_section
    opp2_section = next(t for t in section_texts if "opp-2" in t)
    assert "unopened deck" in opp2_section


def test_header_reports_opportunity_and_signal_counts():
    signals = [
        Signal(signal_type=SignalType.STALLED_DEAL, severity="warning", opportunity_id="opp-1",
               headline="x", detected_at=_NOW),
    ]
    digest = Digest(workspace_id="ws-1", seller_id=None, generated_at=_NOW, opportunity_count=5, signals=signals)
    header = build_slack_blocks(digest)["blocks"][0]["text"]["text"]
    assert "5" in header
    assert "1 signal" in header
