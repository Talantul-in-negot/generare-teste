"""§12 — Context Graph builder response shape."""

from __future__ import annotations

from pydantic import BaseModel

from src.domain.assertion import Claim, Conflict
from src.domain.conversation import ConversationSummary


class EvidenceReference(BaseModel):
    claim_id: str
    source_segment_id: str | None
    evidence_char_start: int
    evidence_char_end: int
    excerpt: str


class SelectedItem(BaseModel):
    claim_id: str
    score: float
    reason: str


class ContextGraphResult(BaseModel):
    workspace_id: str
    claims: list[Claim] = []
    evidence: list[EvidenceReference] = []
    unresolved_mention_ids: list[str] = []
    conflicts: list[Conflict] = []
    selected_items: list[SelectedItem] = []
    budget_max_nodes: int
    budget_max_tokens: int
    nodes_used: int
    tokens_used: int
    truncated: bool
    # Phase 3 dual-layer retrieval: set only when build() was called with
    # include_summary=True, a call_summary_usecase was wired in, and
    # scope.conversation_id was set -- None in every other case, including
    # when a summary couldn't be produced (e.g. no citable Claims). Additive
    # to claims above, never a replacement for them.
    summary: ConversationSummary | None = None
