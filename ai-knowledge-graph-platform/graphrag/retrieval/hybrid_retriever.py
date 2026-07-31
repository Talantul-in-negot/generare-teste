"""Combine local + global search results with configurable weights and re-ranking."""

from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import datetime, timedelta, timezone

import structlog

from graphrag.core.config import get_settings, resolve_tenant_config
from graphrag.core.llm_client import get_llm
from graphrag.core.models import QueryResult
from graphrag.context_graph.models import (
    AgentRun, Case, ContextManifest, Decision, DecisionOption, DecisionTrace,
    OptionDisposition, PolicyEvaluation, PolicyResult, PolicyVersion,
)
from graphrag.context_graph.repository import ContextGraphRepository
from graphrag.graph.contradiction_detector import ContradictionDetector
from graphrag.graph.neo4j_client import get_neo4j
from graphrag.retrieval.local_search import LocalSearch
from graphrag.retrieval.global_search import GlobalSearch
from graphrag.retrieval.context_builder import ContextBuilder
from graphrag.retrieval.agentic_retriever import AgenticRetriever, _is_low_confidence
from graphrag.retrieval.claim_verifier import ClaimVerifier
from graphrag.retrieval.query_rewriter import QueryRewriter
from graphrag.retrieval.session_context import get_session_context

log = structlog.get_logger(__name__)

_ANSWER_PROMPT = """\
You are a regulatory knowledge assistant. Answer using ONLY the information in the context below.
Rules:
- Use ONLY facts stated in the context. Do NOT add information from your training data.
- If a fact is not in the context, do not include it in your answer.
- If the context does not contain enough information to answer, say so explicitly.
- Be concise: 3-5 sentences unless the question requires more.
- State facts directly. Do NOT preface your answer with phrases like "Based on the context", \
"Based solely on the context", "According to the provided context", or similar.
- When a document or procedure has a revision/version number (e.g. "rev.2", "revizia 2", "v.2"), \
state it using the compact document-ID form by removing the dot/space, e.g. "rev.2" -> "rev2". \
Apply this compact form to EVERY revision number you mention, every time you mention it — \
including when you restate the same number later in the answer.
- If a question asks which revision is REFERENCED by one document and whether it matches the \
CURRENT/IN-FORCE revision of another, your answer MUST explicitly state BOTH revision numbers \
in compact form (e.g. "rev2" and "rev4") and explicitly say whether they match or not — do not \
describe the mismatch only in words ("an older revision") without naming both numbers.
- A "=== METADATA ===" block contains a "doc_id" line identifying that chunk's source document \
and revision (e.g. "doc_id: IL-INS-03-rev4"). Treat this as a fact about which revision exists \
when the question concerns document revisions.
- A chunk header may include "Source: <filename>" identifying which document that chunk came \
from. This is for attribution and revision-comparison only — do NOT refuse to use a fact merely \
because its chunk's Source differs from a document named in the question. Use all relevant facts \
from the context to answer fully.
- If the question names specific documents (e.g. "conform X și Y"), and the context contains a \
fact from a chunk whose Source is NOT one of those documents, prefer a fact from a chunk whose \
Source IS one of the named documents when the two conflict.
- A "Community knowledge:" section is a coarse, lower-precision summary. If it conflicts with a \
specific fact stated in a numbered "[Chunk ...]" section above it, the chunk-level fact is more \
reliable — prefer it.
- A "⚠ Unresolved conflicts:" section lists entities/relations where two sources disagree and no \
resolution has been recorded. If your answer touches one of these, explicitly state that sources \
disagree rather than presenting either side as settled fact.

Context:
{context}

Question: {question}

Answer:"""


class HybridRetriever:
    def __init__(self):
        cfg = get_settings()
        self._model_name = cfg.groq_model
        self._cfg = cfg.retrieval
        self._local = LocalSearch()
        self._global = GlobalSearch()
        self._context_builder = ContextBuilder()
        self._contradiction = ContradictionDetector(get_neo4j())
        self._model_version = cfg.groq_model
        self._agentic = AgenticRetriever(
            max_steps=self._cfg.get("agentic_max_steps", 4)
        )
        self._verifier = ClaimVerifier()
        self._rewriter = QueryRewriter()
        self._use_session_ctx = self._cfg.get("session_context_enabled", True)
        self._session_ctx = get_session_context() if self._use_session_ctx else None
        self._context_graph = ContextGraphRepository(get_neo4j())

    async def _record_context_trace(
        self, *, question: str, answer: str, tenant: str, query_id: str,
        mode: str, model_version: str, local_results: dict,
    ) -> None:
        """Persist the evidence-backed query decision for API/worker queries.

        Query IDs are stable across retries in the worker path, making the trace
        idempotent. Direct library calls without a query ID remain side-effect
        free, which keeps CLI and unit-test usage lightweight.
        """
        chunk_ids = list(dict.fromkeys(local_results.get("referenced_chunks", [])))
        if not query_id or not chunk_ids:
            return
        now = datetime.now(timezone.utc)
        later = now + timedelta(days=1)
        digest = hashlib.sha256(f"{tenant}:{query_id}".encode()).hexdigest()[:20]
        case = Case(
            id=f"case-query-{digest}", tenant=tenant, case_type="retrieval_query",
            title="Governed GraphRAG query", description=question,
            valid_from=now, valid_to=later, transaction_from=now, transaction_to=later,
        )
        run = AgentRun(
            id=f"run-query-{digest}", tenant=tenant, case_id=case.id,
            actor_id="graphrag-retriever", model_provider="configured",
            model_version=model_version, prompt_version="hybrid-answer-v1",
            valid_from=now, valid_to=later, transaction_from=now, transaction_to=later,
        )
        policy = PolicyVersion(
            id="policy-retrieval-evidence-v1", tenant=tenant,
            policy_id="retrieval-evidence", version="v1", title="Evidence-grounded retrieval",
            valid_from=now, valid_to=later, transaction_from=now, transaction_to=later,
        )
        manifest = ContextManifest(
            id=f"manifest-query-{digest}", tenant=tenant, case_id=case.id, run_id=run.id,
            chunk_ids=chunk_ids, chunk_versions=["current"] * len(chunk_ids),
            policy_version_ids=[policy.id], model_provider="configured",
            model_version=model_version, prompt_version="hybrid-answer-v1",
            retrieval_mode=mode, retrieval_config={"query_id": query_id}, task_input=question,
            ontology_version="platform/v1", valid_from=now, valid_to=later,
            transaction_from=now, transaction_to=later,
        ).with_integrity_hash()
        decision = Decision(
            id=f"decision-query-{digest}", tenant=tenant, case_id=case.id, run_id=run.id,
            manifest_id=manifest.id, title="GraphRAG answer", selected_option_id=f"option-query-{digest}",
            reason_code="retrieved_evidence", rationale="Answer synthesized from the captured retrieval context.",
            valid_from=now, valid_to=later, transaction_from=now, transaction_to=later,
        )
        option = DecisionOption(
            id=f"option-query-{digest}", tenant=tenant, decision_id=decision.id,
            label="answer", disposition=OptionDisposition.SELECTED,
            reason_code="retrieved_evidence", rationale=answer[:500],
            valid_from=now, valid_to=later, transaction_from=now, transaction_to=later,
        )
        evaluation = PolicyEvaluation(
            id=f"policy-evaluation-query-{digest}", tenant=tenant, decision_id=decision.id,
            policy_version_id=policy.id, result=PolicyResult.ALLOW,
            matched_rule="evidence captured", reason_code="evidence_captured",
            rationale="The retrieval pipeline captured the evidence used for synthesis.",
            valid_from=now, valid_to=later, transaction_from=now, transaction_to=later,
        )
        try:
            await self._context_graph.record_trace(DecisionTrace(
                case=case, run=run, manifest=manifest, policy_versions=[policy],
                policy_evaluations=[evaluation], options=[option], decision=decision,
            ))
        except Exception as exc:
            # Retrieval availability must not depend on Context Graph maintenance.
            log.warning("context_graph.trace_persist_failed", query_id=query_id, error=str(exc)[:200])


    async def retrieve_and_answer(
        self,
        question: str,
        mode: str = "hybrid",
        tenant: str = "default",
        session_id: str = "",
        query_id: str = "",
    ) -> QueryResult:
        t0 = time.monotonic()

        # Per-tenant config: merge this tenant's overrides over the global
        # retrieval defaults (mirrors LocalSearch.search — resolved from
        # self._cfg). Governs the knobs read below: query-rewrite gate, hybrid
        # weights, the context top_k that decides how many chunks reach the LLM,
        # claim verification, agentic fallback. Empty tenant_overrides ⇒ global.
        cfg = resolve_tenant_config(self._cfg, tenant)

        from graphrag.retrieval.result_store import get_result_store
        _store = get_result_store() if query_id else None

        async def _step(msg: str):
            if _store and query_id:
                await _store.push_progress(query_id, msg)

        local_results = {}
        global_results = {}

        # ── Stage 1: query rewrite ─────────────────────────────────────────────
        # Expand/normalize the query for retrieval only. The original `question`
        # is kept for answer synthesis and evaluation — we rewrite what we search
        # with, never what we answer or grade against. Fails open to `question`.
        search_query = question
        if cfg.get("query_rewrite_enabled", True):
            search_query = await self._rewriter.rewrite(question, tenant=tenant)
            if search_query != question:
                await _step(f"📝 Query expanded → {search_query[:60]}")

        if mode == "hybrid":
            # Local and global search share no data dependency, so run them
            # concurrently instead of back-to-back — this hides global
            # search's latency behind local search's rather than adding to
            # it. TaskGroup (not gather) so a failing branch cancels its
            # sibling instead of leaving it orphaned.
            await _step("🔍 BM25 + vector search in graph...")
            await _step("🕸️ GNN scoring — 2-hop traversal...")
            await _step("🕸️ Graph expansion (Leiden communities)...")
            try:
                async with asyncio.TaskGroup() as tg:
                    local_task = tg.create_task(
                        self._local.search(search_query, session_id=session_id, tenant=tenant)
                    )
                    global_task = tg.create_task(
                        self._global.search(search_query, tenant=tenant)
                    )
            except ExceptionGroup as eg:
                # TaskGroup always wraps failures in an ExceptionGroup, even a
                # single one. rabbitmq_client.py logs type(exc).__name__ for
                # DLQ diagnostics — unwrap the common single-failure case so
                # that still sees the real exception type (e.g.
                # APIStatusError), not "ExceptionGroup". Only a genuine
                # double-failure (both branches raising at once) surfaces as
                # a group.
                if len(eg.exceptions) == 1:
                    raise eg.exceptions[0] from eg
                raise
            local_results = local_task.result()
            global_results = global_task.result()
            n_reranked = cfg.get("rerank_top_k", 5)
            await _step(f"📊 Cross-encoder reranking → top {n_reranked} chunks")
        elif mode == "local":
            await _step("🔍 BM25 + vector search in graph...")
            await _step("🕸️ GNN scoring — 2-hop traversal...")
            local_results = await self._local.search(
                search_query,
                session_id=session_id,
                tenant=tenant,
            )
            n_reranked = cfg.get("rerank_top_k", 5)
            await _step(f"📊 Cross-encoder reranking → top {n_reranked} chunks")
        elif mode == "global":
            await _step("🕸️ Graph expansion (Leiden communities)...")
            global_results = await self._global.search(search_query, tenant=tenant)

        # Warn the LLM about entities in this result set that are the subject
        # of an open, unresolved contradiction — otherwise a disputed fact can
        # be retrieved and stated as settled with no signal it's contested.
        # Reuses referenced_entities already computed by LocalSearch.search()
        # — no extra retrieval-stage cost beyond the one Conflict lookup.
        conflicts: list[dict] = []
        if cfg.get("conflict_annotation_enabled", True):
            referenced_entities = local_results.get("referenced_entities", [])
            if referenced_entities:
                conflicts = await self._contradiction.get_open_conflicts_for_entities(
                    referenced_entities, tenant=tenant
                )
                if conflicts:
                    await _step(f"⚠️ {len(conflicts)} unresolved conflict(s) flagged")

        await _step("✍️ Synthesising answer with LLM...")
        context, citations = self._context_builder.build(
            local_results=local_results,
            global_results=global_results,
            weights=(
                cfg.get("hybrid_weight_local", 0.6),
                cfg.get("hybrid_weight_global", 0.4),
            ),
            top_k=cfg.get("rerank_top_k", 5),
            conflicts=conflicts,
        )

        answer = await get_llm().generate(
            _ANSWER_PROMPT.format(context=context, question=question),
        ) or "Insufficient context to answer this question."

        # ── Claim verification — strip ungrounded sentences ────────────────────
        if cfg.get("claim_verification", False):
            answer, n_removed = await self._verifier.verify(answer, context)
            if n_removed:
                log.info("hybrid_retriever.claims_stripped", n_removed=n_removed)

        latency_ms = (time.monotonic() - t0) * 1000

        # ── Record session turn with the real answer ───────────────────────────
        # Done here (not in local_search) so the stored turn always reflects the
        # actual answer shown to the user, making follow-up enrichment faithful.
        if self._use_session_ctx and self._session_ctx and session_id and local_results:
            await self._session_ctx.record_turn(
                session_id=session_id,
                question=question,
                answer=answer,
                referenced_entities=local_results.get("referenced_entities", []),
                referenced_chunks=local_results.get("referenced_chunks", []),
            )

        # ── Agentic fallback ───────────────────────────────────────────────────
        # If the hybrid answer is low-confidence, hand off to the iterative
        # agent which re-searches sub-questions until it accumulates enough
        # context to answer confidently (solves multi-document reasoning).
        agentic_enabled = cfg.get("agentic_fallback", True)
        # Per-tenant: when true, a hedging answer triggers the agent even if it
        # carried citations (see _is_low_confidence). Off by default.
        hedge_only = cfg.get("agentic_hedge_only_fallback", False)
        if agentic_enabled and _is_low_confidence(
            answer, citations, require_no_citations=not hedge_only
        ):
            log.info(
                "hybrid_retriever.low_confidence",
                answer_preview=answer[:80],
                triggering="agentic_fallback",
            )
            result = await self._agentic.retrieve_and_answer(
                question=question,
                initial_context=context,
                initial_citations=citations,
                tenant=tenant,
                session_id=session_id,
            )
            result.latency_ms += latency_ms
            result.query_id = query_id or result.query_id
            await self._record_context_trace(
                question=question, answer=result.answer, tenant=tenant, query_id=query_id,
                mode=result.retrieval_mode, model_version=result.model_version,
                local_results=local_results,
            )
            return result

        log.info("hybrid_retriever.done", mode=mode, latency_ms=round(latency_ms, 1))

        result = QueryResult(
            question=question,
            answer=answer,
            # `context` is the full string fed to the synthesis LLM (local chunks +
            # entity context + global community knowledge). Using only local chunks
            # here caused RAGAS to judge claims grounded in "Community knowledge"
            # as unsupported (faithfulness=0.0 false negatives, e.g. AUT-03).
            contexts=[context] if context else [],
            citations=citations,
            latency_ms=latency_ms,
            retrieval_mode=mode,
            model_version=self._model_version,
        )
        if query_id:
            result.query_id = query_id
        await self._record_context_trace(
            question=question, answer=answer, tenant=tenant, query_id=query_id,
            mode=mode, model_version=self._model_version, local_results=local_results,
        )
        return result
