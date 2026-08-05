"""Slack Block Kit digest formatting + delivery via an Incoming Webhook.

build_slack_blocks is pure (dict in, dict out) and carries all the tests; only
post_digest touches the network, via httpx (already a dependency — see
docker-compose.yml/api/state.py's precedent for not adding a new HTTP client
just for this).
"""

from __future__ import annotations

import httpx

from src.signals.models import Signal, SignalType
from src.usecases.digest import Digest

_SEVERITY_EMOJI = {"warning": "⚠️", "info": "ℹ️"}

_SIGNAL_LABELS = {
    SignalType.SINGLE_THREADED_DEAL: "Single-threaded",
    SignalType.OBJECTION_WITHOUT_FOLLOW_UP: "Objection unanswered",
    SignalType.SHARED_CONTENT_NEVER_OPENED: "Content unopened",
    SignalType.UNRESOLVED_CONFLICT: "Unresolved conflict",
    SignalType.STALLED_DEAL: "Stalled deal",
}


def build_slack_blocks(digest: Digest) -> dict:
    header_text = (
        f"Sales digest — {digest.opportunity_count} open "
        f"{'deal' if digest.opportunity_count == 1 else 'deals'}, {len(digest.signals)} signal(s)"
    )
    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": header_text}},
    ]

    if not digest.signals:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "No signals — nothing needs attention right now."},
        })
        return {"blocks": blocks}

    for opportunity_id, group in _group_by_opportunity(digest.signals).items():
        lines = "\n".join(f"{_line(s)}" for s in group)
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Opportunity `{opportunity_id}`*\n{lines}"},
        })

    return {"blocks": blocks}


def _line(signal: Signal) -> str:
    emoji = _SEVERITY_EMOJI.get(signal.severity, "")
    label = _SIGNAL_LABELS.get(signal.signal_type, signal.signal_type.value)
    return f"{emoji} *{label}* — {signal.headline}"


def _group_by_opportunity(signals: list[Signal]) -> dict[str, list[Signal]]:
    grouped: dict[str, list[Signal]] = {}
    for s in signals:
        grouped.setdefault(s.opportunity_id, []).append(s)
    return grouped


async def post_digest(webhook_url: str, payload: dict) -> None:
    async with httpx.AsyncClient() as client:
        response = await client.post(webhook_url, json=payload, timeout=10.0)
        response.raise_for_status()
