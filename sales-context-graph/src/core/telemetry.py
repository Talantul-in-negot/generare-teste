"""Prometheus metrics for the 9 named observability targets in
docs/plan.md Section 14 ("Metrics:" bullet list). Every metric below is
instantiated once at import time; call sites increment/observe it directly
rather than going through an indirection layer, matching how
`src/graph/alias_registry.py` and friends already reach for module-level
singletons rather than a DI container.

Cardinality note (docs/plan.md Section 14, verbatim): "Avoid
unbounded-cardinality metric labels. Workspace-level operational detail
should be available through controlled logs/traces or bounded reporting,
not arbitrary metric labels at large tenant counts." None of the metrics
below carry `workspace_id` (or any other high-cardinality field) as a
label for exactly that reason -- per-tenant detail belongs in structured
logs (src/core/logging.py), not here. Every label used has a small, fixed
set of values (job kind, decision status, truncation reason, etc).

Serving: GET /metrics (api/main.py) renders the default
`prometheus_client` registry via `generate_latest()` -- no collector
service, no new deployed infrastructure, a plain scrape target.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# --- 1. Ingestion count and duration by status -----------------------------
INGESTION_JOBS_TOTAL = Counter(
    "scg_ingestion_jobs_total",
    "Ingestion jobs that reached a terminal state, by kind and status.",
    ["kind", "status"],
)
INGESTION_JOB_DURATION_SECONDS = Histogram(
    "scg_ingestion_job_duration_seconds",
    "Ingestion job processing duration in seconds, by kind.",
    ["kind"],
)

# --- 2/3. Extraction windows, provider calls, failures and retries ---------
EXTRACTION_WINDOWS_TOTAL = Counter(
    "scg_extraction_windows_total",
    "Extraction windows built from a transcript (src/extraction/windowing.py).",
)
EXTRACTION_PROVIDER_CALLS_TOTAL = Counter(
    "scg_extraction_provider_calls_total",
    "LLM extraction provider call attempts, by outcome.",
    ["outcome"],  # success | retry | permanent_failure
)

# --- 4. Candidate-generation latency ----------------------------------------
CANDIDATE_GENERATION_DURATION_SECONDS = Histogram(
    "scg_candidate_generation_duration_seconds",
    "Entity-resolution candidate generation latency: DB fetch plus "
    "blocking/narrowing (src/resolution/pipeline.py's union_candidates call).",
)

# --- 5. Blocking recall (evaluation runs only) ------------------------------
BLOCKING_RECALL = Gauge(
    "scg_blocking_recall",
    "Fraction of gold-standard entities retained after blocking, from the "
    "most recently scored evaluation run. There is no ground truth in live "
    "traffic to measure recall against -- this is set by an eval harness "
    "(tests/eval/*), not sampled from request handling. See "
    "record_blocking_recall() below.",
)

# --- 6. Auto-link, review, unresolved, and rejection counts ----------------
RESOLUTION_DECISIONS_TOTAL = Counter(
    "scg_resolution_decisions_total",
    "Mention resolution decisions, by outcome (src/resolution/policy.py::decide).",
    ["status"],  # auto_linked | pending_review | unresolved | rejected
)

# --- 7. Claims created, superseded, conflicted, and erased -----------------
CLAIMS_TOTAL = Counter(
    "scg_claims_total",
    "Claim lifecycle events, by event type.",
    ["event"],  # created | superseded | conflicted | erased
)

# --- 8. Context Graph latency, result count, and budget truncation ---------
CONTEXT_GRAPH_BUILD_DURATION_SECONDS = Histogram(
    "scg_context_graph_build_duration_seconds",
    "src/context_graph/builder.py ContextGraphBuilder.build() latency.",
)
CONTEXT_GRAPH_RESULT_COUNT = Histogram(
    "scg_context_graph_result_count",
    "Claims selected into a built Context Graph.",
    buckets=(1, 2, 5, 10, 20, 50, 100),
)
CONTEXT_GRAPH_TRUNCATED_TOTAL = Counter(
    "scg_context_graph_truncated_total",
    "Context Graph builds that hit a budget cap before exhausting the "
    "scored candidate list, by which cap was hit.",
    ["reason"],  # max_nodes | max_tokens
)

# --- 9. Queue depth and oldest-job age --------------------------------------
INGESTION_QUEUE_DEPTH = Gauge(
    "scg_ingestion_queue_depth",
    "Jobs currently waiting in the ingestion queue (src/ingestion/queue.py).",
)
INGESTION_QUEUE_OLDEST_JOB_AGE_SECONDS = Gauge(
    "scg_ingestion_queue_oldest_job_age_seconds",
    "Age in seconds of the oldest job still waiting in the ingestion "
    "queue. 0 when the queue is empty.",
)

# --- Phase 6 addition: prompt-injection guardrail flags ---------------------
# Not one of docs/plan.md §14's original 9 -- added when the guardrail
# itself was (src/extraction/guardrail.py), same "count every real signal
# this system produces" discipline as the rest of this file.
GUARDRAIL_FLAG_TOTAL = Counter(
    "scg_guardrail_flag_total",
    "Extraction windows flagged by the prompt-injection heuristic guardrail, "
    "regardless of enforcement mode (log_only vs block).",
)


def record_blocking_recall(value: float) -> None:
    """Called by tests/eval/* after scoring a labeled run's blocking output
    against ground truth. Not invoked from any request-handling path."""
    BLOCKING_RECALL.set(value)
