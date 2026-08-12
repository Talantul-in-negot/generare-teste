"""§11 — 'For the MVP, an in-process bounded worker may be used, but its
interface must support later replacement with a durable queue.' InMemoryIngestionStore
below is that in-process store, and its limitation is real, not hidden: a process
restart loses every job (tests/unit/api/test_ingestion_store.py proves it — never
claim restart safety with process memory only). It's the local-dev fallback used
when REDIS_URL is unset; get_ingestion_store() below prefers RedisIngestionStore
whenever Redis is configured, which is the case in every deployed environment.

Both classes share one async get(id)/put(job) interface so api/routes/ingestions.py
never needs to know which backend is active — see get_ingestion_store().
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime

from src.domain.enums import IngestionState


@dataclass
class IngestionJob:
    ingestion_id: str
    workspace_id: str
    kind: str  # "crm" | "content-assets" | "transcripts"
    state: IngestionState
    created_at: datetime
    updated_at: datetime
    item_results: list[dict] = field(default_factory=list)
    error: str | None = None
    # Transcript fan-out progress. Both stay 0 for kinds that aren't windowed
    # (crm, content-assets, engagement) -- a caller reads windows_total == 0 as
    # "this job kind has no window progress to report", not as "no progress".
    # Defaulted (not required) so records written before this field existed
    # still deserialize -- see _deserialize's .get() calls.
    windows_total: int = 0
    windows_processed: int = 0


# §11's IngestionState is deliberately richer than a generic job status
# (NORMALIZING/EXTRACTING/RESOLVING/PERSISTING are real, separately observable
# phases). API clients polling for "is it done yet" should not have to know
# that vocabulary, nor should adding a phase later break them -- so the route
# exposes this coarse projection *alongside* the precise state rather than
# replacing it. Mapping is intentionally total: every enum member maps.
_COARSE_STATUS: dict[IngestionState, str] = {
    IngestionState.ACCEPTED: "queued",
    IngestionState.NORMALIZING: "running",
    IngestionState.EXTRACTING: "running",
    IngestionState.RESOLVING: "running",
    IngestionState.PERSISTING: "running",
    IngestionState.COMPLETED: "completed",
    # Completed, with items flagged for human review -- the job itself did
    # finish, so "completed" is honest; the review flag lives in item_results.
    IngestionState.COMPLETED_WITH_REVIEW: "completed",
    # Retryable means the worker will try again, so it is still in flight from
    # the caller's perspective -- reporting "failed" here would be wrong until
    # the attempt budget is actually exhausted (at which point _run promotes
    # the job to FAILED_PERMANENT).
    IngestionState.FAILED_RETRYABLE: "running",
    IngestionState.FAILED_PERMANENT: "failed",
}


def coarse_status(state: IngestionState) -> str:
    """Project §11's lifecycle onto queued|running|completed|failed."""
    return _COARSE_STATUS[state]


# IngestionJob.error is returned verbatim by GET /api/v1/ingestions/{id}, so it
# is a trust boundary: a raw str(exc) there can leak a Cypher fragment, an
# upstream vendor response body, a filesystem path, or a key fragment embedded
# in a client URL. These are the only two values written to that field. Full
# exception detail still reaches the logs and the DLQ message, which stay
# operator-side. Defined here, beside the record they populate, so the
# synchronous route path and the queued worker path cannot drift apart.
SAFE_PERMANENT_INGESTION_ERROR = (
    "ingestion failed: the submitted payload could not be processed. "
    "Check the request shape against the API contract and resubmit."
)
SAFE_RETRYABLE_INGESTION_ERROR = (
    "ingestion failed after repeated attempts due to a temporary "
    "downstream error. The payload was accepted; retry later."
)


class InMemoryIngestionStore:
    """Local-dev fallback only — see this module's docstring. Not what a real
    deployment uses once REDIS_URL is set."""

    def __init__(self):
        self._jobs: dict[str, IngestionJob] = {}

    async def put(self, job: IngestionJob) -> None:
        self._jobs[job.ingestion_id] = job

    async def get(self, ingestion_id: str) -> IngestionJob | None:
        return self._jobs.get(ingestion_id)


_KEY_PREFIX = "scg:ingestion:"
_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days — job records aren't kept forever


def _serialize(job: IngestionJob) -> str:
    data = asdict(job)
    data["state"] = job.state.value
    data["created_at"] = job.created_at.isoformat()
    data["updated_at"] = job.updated_at.isoformat()
    return json.dumps(data)


def _deserialize(raw: str) -> IngestionJob:
    data = json.loads(raw)
    return IngestionJob(
        ingestion_id=data["ingestion_id"],
        workspace_id=data["workspace_id"],
        kind=data["kind"],
        state=IngestionState(data["state"]),
        created_at=datetime.fromisoformat(data["created_at"]),
        updated_at=datetime.fromisoformat(data["updated_at"]),
        item_results=data["item_results"],
        error=data["error"],
        # .get(): job records written before progress tracking existed are
        # still in Redis under a 30-day TTL, and must not fail to load.
        windows_total=data.get("windows_total", 0),
        windows_processed=data.get("windows_processed", 0),
    )


class RedisIngestionStore:
    """Durable replacement for InMemoryIngestionStore — same get(id)/put(job)
    interface, now async (Redis I/O is inherently async). Jobs are stored as a
    JSON blob keyed by ingestion_id only, not workspace-namespaced — the
    caller (api/routes/ingestions.py::get_ingestion) still fetches by id then
    compares job.workspace_id itself, exactly as with the in-memory store, so
    the existing same-404-for-cross-workspace behavior is unchanged.
    """

    def __init__(self, redis_client):
        self._redis = redis_client

    async def put(self, job: IngestionJob) -> None:
        await self._redis.set(_KEY_PREFIX + job.ingestion_id, _serialize(job), ex=_TTL_SECONDS)

    async def get(self, ingestion_id: str) -> IngestionJob | None:
        raw = await self._redis.get(_KEY_PREFIX + ingestion_id)
        return _deserialize(raw) if raw is not None else None


def get_ingestion_store():
    from src.core.redis_client import get_redis

    redis_client = get_redis()
    return RedisIngestionStore(redis_client) if redis_client is not None else InMemoryIngestionStore()
