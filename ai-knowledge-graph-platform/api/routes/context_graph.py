"""Context Graph P0 endpoints: validation, persistence, and WPP trace slice."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.auth.dependencies import require_scope
from graphrag.context_graph.models import (
    CGAction, CGApproval, CGCorrection, CGExceptionGrant, CGFeedback, CGOutcome,
    DecisionTrace,
)
from graphrag.context_graph.proactive import ProactiveContextService
from graphrag.context_graph.repository import ContextGraphRepository
from graphrag.context_graph.trace_service import ContextGraphTraceService
from graphrag.context_graph.validation import validate_trace
from graphrag.graph.neo4j_client import get_neo4j

router = APIRouter(prefix="/context-graph", tags=["Context Graph P0"])


class TraceRequest(BaseModel):
    trace: DecisionTrace


@router.post("/traces/validate", dependencies=[Depends(require_scope("read"))])
async def validate_context_trace(request: TraceRequest):
    validate_trace(request.trace)
    return {"valid": True, "decision_id": request.trace.decision.id,
            "integrity_hash": request.trace.manifest.integrity_hash}


@router.post("/traces", dependencies=[Depends(require_scope("write"))])
async def record_context_trace(request: TraceRequest):
    decision_id = await ContextGraphRepository(get_neo4j()).record_trace(request.trace)
    return {"decision_id": decision_id, "tenant": request.trace.case.tenant,
            "schema_version": request.trace.case.schema_version}


@router.get("/traces/{decision_id}", dependencies=[Depends(require_scope("read"))])
async def load_context_trace(decision_id: str, tenant: str = "default"):
    return await ContextGraphRepository(get_neo4j()).load_trace(decision_id, tenant)


class WPPTraceRequest(BaseModel):
    placement_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    statement_ids: list[str] = Field(min_length=1)
    statement_versions: list[str] = Field(min_length=1)
    chunk_ids: list[str] = Field(default_factory=list)
    chunk_versions: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    document_versions: list[str] = Field(default_factory=list)
    selected: str = "escalate"
    policy_id: str = "data-privacy-policy"
    policy_version: str = "2024.1"
    tenant: str = "marketing"


@router.post("/wpp/campaign-placement", dependencies=[Depends(require_scope("write"))])
async def record_wpp_campaign_trace(request: WPPTraceRequest):
    service = ContextGraphTraceService(ContextGraphRepository(get_neo4j()))
    decision_id = await service.record_wpp_campaign_placement(**request.model_dump())
    return {"decision_id": decision_id, "tenant": request.tenant,
            "scenario": "wpp_campaign_placement"}


@router.post("/governance/events", dependencies=[Depends(require_scope("write"))])
async def append_governance_event(event: CGApproval | CGExceptionGrant | CGCorrection):
    event_id = await ContextGraphRepository(get_neo4j()).append_governance_event(event)
    return {"event_id": event_id, "tenant": event.tenant}


@router.post("/actions", dependencies=[Depends(require_scope("write"))])
async def record_action(action: CGAction):
    return {"action_id": await ContextGraphRepository(get_neo4j()).record_action(action)}


@router.post("/outcomes", dependencies=[Depends(require_scope("write"))])
async def record_outcome(outcome: CGOutcome):
    return {"outcome_id": await ContextGraphRepository(get_neo4j()).record_outcome(outcome)}


@router.post("/feedback", dependencies=[Depends(require_scope("write"))])
async def record_feedback(feedback: CGFeedback):
    return {"feedback_id": await ContextGraphRepository(get_neo4j()).record_feedback(feedback)}


@router.get("/traces/{decision_id}/replay", dependencies=[Depends(require_scope("read"))])
async def replay_context_trace(decision_id: str, as_of: str, tenant: str = "default"):
    return await ContextGraphRepository(get_neo4j()).replay_trace(decision_id, tenant, as_of)


@router.get("/precedents", dependencies=[Depends(require_scope("read"))])
async def find_context_precedents(policy_version_id: str, tenant: str = "default", limit: int = 10):
    return await ContextGraphRepository(get_neo4j()).find_precedents(tenant, policy_version_id, limit)


@router.get("/proactive/expiring-policies", dependencies=[Depends(require_scope("read"))])
async def expiring_context_policies(tenant: str = "default", within_days: int = 30):
    return [item.model_dump(mode="json") for item in await ProactiveContextService(get_neo4j()).expiring_policies(tenant, within_days)]
