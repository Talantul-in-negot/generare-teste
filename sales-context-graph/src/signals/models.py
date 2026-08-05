from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class SignalType(StrEnum):
    SINGLE_THREADED_DEAL = "single_threaded_deal"
    OBJECTION_WITHOUT_FOLLOW_UP = "objection_without_follow_up"
    SHARED_CONTENT_NEVER_OPENED = "shared_content_never_opened"
    UNRESOLVED_CONFLICT = "unresolved_conflict"
    STALLED_DEAL = "stalled_deal"


class Signal(BaseModel):
    signal_type: SignalType
    severity: str  # "info" | "warning" — deliberately not a bare bool; leaves
                   # room for a future third tier without a breaking change
    opportunity_id: str
    headline: str
    evidence_claim_ids: list[str] = []
    evidence_share_ids: list[str] = []
    detected_at: datetime
