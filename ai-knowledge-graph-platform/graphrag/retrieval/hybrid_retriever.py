"""Combine local + global search results with configurable weights and re-ranking."""

from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import datetime, timedelta, timezone

import structlog

from graphrag.core.config import get_settings, resolve_tenant_config
from graphrag.core.llm_client import get_generation_route, get_llm
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
from graphrag.retrieval.feedback import RetrievalFeedbackService, apply_feedback_scores
from graphrag.retrieval.query_planner import retrieval_plan
from graphrag.observability.budgets import check_budget
from graphrag.observability.cost_attribution import CostEvent, record_cost_event
from graphrag.retrieval.session_context import get_session_context
from graphrag.retrieval.query_cache import (
    QueryCacheContext,
    build_cache_key,
    get_query_cache,
)

log = structlog.get_logger(__name__)

_PROMPT_VERSION = "hybrid-answer-v1"
_ONTOLOGY_VERSION = "platform/v1"
_NON_SEMANTIC_RETRIEVAL_KEYS = {
    "redis_url",
    "query_result_ttl_seconds",
    "semantic_answer_cache_enabled",
    "semantic_answer_cache_ttl_seconds",
    "session_store",
    "session_store_strict",
    "session_ttl_seconds",
}

_RETRIEVAL_PROFILE_OVERRIDES: dict[str, dict] = {
    "full": {},
    "vector_only": {
        "bm25_enabled": False,
        "reranker_enabled": False,
        "multihop_depth": 0,
        "gnn_enabled": False,
        "pagerank_tiebreak_enabled": False,
        "authority_weighting_enabled": False,
        "feedback_ranking_enabled": False,
        "query_rewrite_enabled": False,
        "entity_context_enabled": False,
        "named_document_boost_enabled": False,
    },
    "text_hybrid": {
        "multihop_depth": 0,
        "gnn_enabled": False,
        "pagerank_tiebreak_enabled": False,
        "authority_weighting_enabled": False,
        "feedback_ranking_enabled": False,
        "entity_context_enabled": False,
        "named_document_boost_enabled": False,
    },
    "global_map_reduce": {"global_search_strategy": "map_reduce"},
    "dual_level_direct": {"global_search_strategy": "direct_context"},
}


def retrieval_profile_overrides(profile: str) -> dict:
    """Return a named, reproducible retrieval profile for evaluation."""
    try:
        return dict(_RETRIEVAL_PROFILE_OVERRIDES[profile])
    except KeyError as exc:
        raise ValueError(
            f"Unknown retrieval profile {profile!r}; expected one of "
            f"{', '.join(sorted(_RETRIEVAL_PROFILE_OVERRIDES))}"
        ) from exc


def _cache_retrieval_config(cfg: dict) -> dict:
    """Keep output-affecting retrieval knobs in the content-addressed key."""
    return {
        key: value for key, value in cfg.items()
        if key not in _NON_SEMANTIC_RETRIEVAL_KEYS
    }

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
        self._feedback = RetrievalFeedbackService(get_neo4j())

    async def _apply_retrieval_feedback(
        self, local_results: dict, tenant: str, cfg: dict,
    ) -> None:
        if not local_results or not cfg.get("feedback_ranking_enabled", True):
            return
        citation_ids: list[str] = []
        for chunk in local_results.get("chunks", []):
            citation_ids.append(str(chunk.get("chunk_id", "")))
            if chunk.get("_doc_name"):
                citation_ids.append(str(chunk["_doc_name"]))
            source = str(chunk.get("source", ""))
            if source:
                citation_ids.append(source.rsplit(".", 1)[0])
        try:
            scores = await self._feedback.scores(citation_ids, tenant)
            apply_feedback_scores(
                local_results, scores, float(cfg.get("feedback_ranking_weight", 0.10))
            )
        except Exception as exc:
            log.warning("hybrid_retriever.feedback_ranking_failed", error=str(exc)[:200])

    async def _record_context_trace(
        self, *, question: str, answer: str, tenant: str, query_id: str,
        mode: str, model_version: str, local_results: dict,
        cache_context: QueryCacheContext | None = None,
        valid_at: str | None = None,
        transaction_at: str | None = None,
    ) -> str | None:
        """Persist the evidence-backed query decision for API/worker queries.

        Query IDs are stable across retries in the worker path, making the trace
        idempotent. Direct library calls without a query ID remain side-effect
        free, which keeps CLI and unit-test usage lightweight.
        """
        chunk_ids = list(dict.fromkeys(local_results.get("referenced_chunks", [])))
        if not query_id or not chunk_ids:
            return None
        # Preserve the document lineage of retrieved chunks in the manifest so
        # a replay can show the same evidence at both KG levels.
        document_rows = await get_neo4j().run(
            "MATCH (c:Chunk {tenant: $tenant}) "
            "WHERE c.id IN $chunk_ids "
            "RETURN DISTINCT c.document_id AS document_id "
            "ORDER BY document_id",
            tenant=tenant,
            chunk_ids=chunk_ids,
        )
        document_ids = [str(row["document_id"]) for row in document_rows if row.get("document_id")]
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
            model_version=model_version, prompt_version=_PROMPT_VERSION,
            valid_from=now, valid_to=later, transaction_from=now, transaction_to=later,
        )
        policy = PolicyVersion(
            id="policy-retrieval-evidence-v1", tenant=tenant,
            policy_id="retrieval-evidence", version="v1", title="Evidence-grounded retrieval",
            valid_from=now, valid_to=later, transaction_from=now, transaction_to=later,
        )
        retrieval_config: dict = {
            "query_id": query_id,
            "temporal_query": {
                "valid_at": valid_at,
                "transaction_at": transaction_at,
            },
        }
        if cache_context is not None:
            retrieval_config.update({
                "answer_cache_key": build_cache_key(question, tenant, cache_context),
                "cache_schema_version": cache_context.cache_schema_version,
                "corpus_revision": cache_context.corpus_revision,
                "requested_mode": cache_context.requested_mode,
                "model_route": cache_context.model_route,
                "effective_retrieval_config": cache_context.retrieval_config,
            })
        manifest = ContextManifest(
            id=f"manifest-query-{digest}", tenant=tenant, case_id=case.id, run_id=run.id,
            chunk_ids=chunk_ids, chunk_versions=["current"] * len(chunk_ids),
            document_ids=document_ids, document_versions=["current"] * len(document_ids),
            policy_version_ids=[policy.id], model_provider="configured",
            model_version=model_version, prompt_version=_PROMPT_VERSION,
            retrieval_mode=mode, retrieval_config=retrieval_config, task_input=question,
            ontology_version=_ONTOLOGY_VERSION, valid_from=now, valid_to=later,
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
            return await self._context_graph.record_trace(DecisionTrace(
                case=case, run=run, manifest=manifest, policy_versions=[policy],
                policy_evaluations=[evaluation], options=[option], decision=decision,
            ))
        except Exception as exc:
            # Retrieval availability must not depend on Context Graph maintenance.
            log.warning("context_graph.trace_persist_failed", query_id=query_id, error=str(exc)[:200])
            return None


    async def retrieve_and_answer(
        self,
        question: str,
        mode: str = "hybrid",
        tenant: str = "default",
        session_id: str = "",
        query_id: str = "",
        valid_at: str | None = None,
        transaction_at: str | None = None,
        retrieval_profile: str = "full",
    ) -> QueryResult:
        t0 = time.monotonic()
        requested_mode = mode

        # Per-tenant config: merge this tenant's overrides over the global
        # retrieval defaults (mirrors LocalSearch.search — resolved from
        # self._cfg). Governs the knobs read below: query-rewrite gate, hybrid
        # weights, the context top_k that decides how many chunks reach the LLM,
        # claim verification, agentic fallback. Empty tenant_overrides ⇒ global.
        profile_overrides = retrieval_profile_overrides(retrieval_profile)
        cfg = {**resolve_tenant_config(self._cfg, tenant), **profile_overrides}
        if retrieval_profile == "vector_only":
            mode = "local"
        if (valid_at or transaction_at) and mode in {"hybrid", "global"}:
            # Community summaries have no versioned evidence lineage, so they
            # cannot safely answer a point-in-time question. Use the temporal
            # chunk/edge path until community snapshots are versioned.
            mode = "local"
            log.info("hybrid_retriever.global_skipped", reason="temporal_query")
        plan = retrieval_plan(question)
        if mode == "hybrid" and cfg.get("query_planner_enabled", False):
            mode = plan["mode"]
            cfg = {**cfg, "local_top_k": plan["top_k"]}
            log.info("hybrid_retriever.query_plan", query_class=plan["query_class"], mode=mode,
                     top_k=plan["top_k"], fallback=plan["fallback"])

        from graphrag.retrieval.result_store import get_result_store
        _store = get_result_store() if query_id else None

        async def _step(msg: str):
            if _store and query_id:
                await _store.push_progress(query_id, msg)

        answer_cache = None
        cache_context = None
        if (
            query_id
            and not session_id
            and cfg.get("semantic_answer_cache_enabled", False)
        ):
            try:
                corpus_state = await get_neo4j().get_corpus_state(tenant)
                if not corpus_state.get("updating", False):
                    cache_context = QueryCacheContext(
                        corpus_revision=int(corpus_state.get("revision", 0)),
                        requested_mode=requested_mode,
                        effective_mode=mode,
                        model_route=get_generation_route(),
                        prompt_version=_PROMPT_VERSION,
                        retrieval_config=_cache_retrieval_config(cfg),
                        ontology_version=_ONTOLOGY_VERSION,
                        valid_at=valid_at,
                        transaction_at=transaction_at,
                    )
                    answer_cache = await get_query_cache()
                    cached = await answer_cache.get(question, tenant, cache_context)
                    if cached:
                        result = QueryResult.model_validate(cached["result"])
                        result.query_id = query_id
                        result.question = question
                        result.cache_hit = True
                        result.cache_key = cached["cache_key"]
                        result.source_query_id = cached["source_query_id"]
                        result.source_trace_id = cached["source_trace_id"]
                        result.latency_ms = (time.monotonic() - t0) * 1000
                        await _step("Answer cache hit; original governed trace reused")
                        record_cost_event(CostEvent(
                            tenant=tenant, stage="answer_cache", provider="redis",
                            model=result.model_version, cost_usd=0.0,
                            latency_ms=result.latency_ms,
                        ))
                        return result
                else:
                    log.info("query_cache.bypassed_corpus_updating", tenant=tenant)
            except Exception as exc:
                log.warning("query_cache.lookup_failed", tenant=tenant, error=str(exc)[:200])

        async def _store_governed_result(result: QueryResult, trace_id: str | None) -> None:
            if not answer_cache or not cache_context or not trace_id or not result.citations:
                return
            key = await answer_cache.set(
                question,
                tenant,
                cache_context,
                result.model_dump(mode="json"),
                source_query_id=query_id,
                source_trace_id=trace_id,
                entities_used=list(result.citations),
            )
            result.cache_key = key
            result.source_query_id = query_id
            result.source_trace_id = trace_id

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
                        self._local.search(
                            search_query,
                            session_id=session_id,
                            tenant=tenant,
                            valid_at=valid_at,
                            transaction_at=transaction_at,
                            config_overrides=profile_overrides,
                        )
                    )
                    global_task = tg.create_task(
                        self._global.search(
                            search_query,
                            tenant=tenant,
                            valid_at=valid_at,
                            transaction_at=transaction_at,
                            config_overrides=profile_overrides,
                        )
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
                valid_at=valid_at,
                transaction_at=transaction_at,
                config_overrides=profile_overrides,
            )
            n_reranked = cfg.get("rerank_top_k", 5)
            await _step(f"📊 Cross-encoder reranking → top {n_reranked} chunks")
        elif mode == "global":
            await _step("🕸️ Graph expansion (Leiden communities)...")
            global_results = await self._global.search(
                search_query,
                tenant=tenant,
                valid_at=valid_at,
                transaction_at=transaction_at,
                config_overrides=profile_overrides,
            )

        await self._apply_retrieval_feedback(local_results, tenant, cfg)

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
        budget = check_budget("synthesis", latency_ms, 0.0)
        record_cost_event(CostEvent(
            tenant=tenant, stage="synthesis", provider="configured",
            model=self._model_version, cost_usd=0.0, latency_ms=latency_ms,
        ))
        if not budget["within_budget"]:
            log.warning("hybrid_retriever.budget_exceeded", **budget)

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
        agentic_enabled = cfg.get("agentic_fallback", True) and not (valid_at or transaction_at)
        if cfg.get("agentic_fallback", True) and (valid_at or transaction_at):
            log.info("hybrid_retriever.agentic_skipped", reason="temporal_query")
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
            result.valid_at = valid_at
            result.transaction_at = transaction_at
            trace_id = await self._record_context_trace(
                question=question, answer=result.answer, tenant=tenant, query_id=query_id,
                mode=result.retrieval_mode, model_version=result.model_version,
                local_results=local_results, cache_context=cache_context,
                valid_at=valid_at, transaction_at=transaction_at,
            )
            # The agentic retriever can add evidence not represented in
            # local_results yet. Do not cache it until that complete evidence
            # set is available to the governed trace.
            result.source_trace_id = trace_id or ""
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
            valid_at=valid_at,
            transaction_at=transaction_at,
        )
        if query_id:
            result.query_id = query_id
        trace_id = await self._record_context_trace(
            question=question, answer=answer, tenant=tenant, query_id=query_id,
            mode=mode, model_version=self._model_version, local_results=local_results,
            cache_context=cache_context,
            valid_at=valid_at, transaction_at=transaction_at,
        )
        await _store_governed_result(result, trace_id)
        return result
