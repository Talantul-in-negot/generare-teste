# Forked from ai-knowledge-graph-platform (graphrag/core/config.py) — trimmed to
# sales-context-graph's actual env surface (see .env.example). Dropped fields with
# no analog here: google/openai/deepseek/groq-specific knobs, wikidata_linking_enabled,
# llm_cache_enabled, llm_ingest_provider, rabbitmq_url, session/oauth/cors settings
# (a real IdP is still deferred per docs/plan.md §13 — workspace_api_keys below is
# an MVP API-key-per-workspace stand-in, not that).
#
# _load_yaml() no longer crashes when config/settings.yml is absent (it is, until
# a later phase's ontology work adds one) — it now fails open to {} with a warning,
# instead of the original's bare open()/FileNotFoundError.

"""Load settings.yml (if present) + .env into a typed Settings object."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import structlog
import yaml
from pydantic import model_validator
from pydantic_settings import BaseSettings

log = structlog.get_logger(__name__)

ROOT = Path(__file__).resolve().parents[2]  # repo root


def _load_yaml() -> dict:
    path = ROOT / "config" / "settings.yml"
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except OSError as exc:
        log.warning("config.settings_yaml_load_failed", path=str(path), error=str(exc))
        return {}


class Settings(BaseSettings):
    # ── LLM provider for structured extraction (P3) + the Increment 15 LLM layer ──
    # llm_provider="anthropic" + a non-empty llm_api_key is what src/llm/chat.py
    # requires to build a real ChatFn. Left blank, every LLM-backed route returns
    # 503 rather than a fabricated answer (see src/llm/chat.py).
    llm_provider: str = ""
    llm_api_key: str = ""
    llm_model: str = "claude-sonnet-5"
    llm_max_output_tokens: int = 2048

    # ── Embedding provider (candidate generation / vector retrieval) ─────────────
    embedding_provider: str = ""
    embedding_api_key: str = ""

    # ── Neo4j ─────────────────────────────────────────────────────────────────────
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "scg_dev_local"

    # ── Auth (MVP: API key per workspace, see api/dependencies.py::verify_api_key) ─
    # JSON map workspace_id -> secret key, e.g. WORKSPACE_API_KEYS='{"ws-demo":"..."}'.
    # pydantic-settings JSON-decodes dict-typed fields from a single env var.
    workspace_api_keys: dict[str, str] = {}

    # ── Redis (durable ingestion job store, see api/state.py::get_ingestion_store) ─
    # Empty means "no Redis configured" -> falls back to InMemoryIngestionStore.
    redis_url: str = ""

    # Durable ingestion execution is opt-in for local development and must be
    # enabled in production together with the worker service.
    ingestion_queue_enabled: bool = False
    ingestion_queue_max_attempts: int = 3
    ingestion_worker_heartbeat_seconds: int = 60
    # Phase 4 (docs/evaluation.md's ingestion-reliability item, ADR-0001's
    # addendum) -- SQS-style visibility timeout: how long a job may sit
    # claimed-but-unfinished in a worker's own processing list before the
    # reaper assumes that worker crashed and puts it back on the main
    # queue. Must comfortably exceed the slowest real job (transcript
    # extraction, the only LLM-backed ingestion kind) -- 5 minutes default.
    ingestion_visibility_timeout_seconds: int = 300

    # ── Proactive digest (Increment 17, see src/usecases/digest.py) ──────────────
    # Empty slack_webhook_url means POST /api/v1/digest/deliver returns 503 rather
    # than posting nowhere; GET /api/v1/digest (JSON, no delivery) works regardless.
    slack_webhook_url: str = ""
    digest_stale_share_days: int = 7
    digest_stalled_deal_days: int = 21

    # ── Embeddable panel (Increment 20, see api/routes/viz.py's /viz/panel) ──────
    # Space-separated list of origins allowed to iframe /viz/panel (sets
    # Content-Security-Policy: frame-ancestors). Empty means no origin is
    # allowed to embed it — the panel still works when opened directly, but
    # embedding is denied by default rather than left open.
    embed_allowed_origins: str = ""

    # HMAC secret for the panel token (src/viz/panel_tokens.py) that replaced
    # putting the real workspace API key in /viz/panel's URL (docs/
    # evaluation.md's Showpad-compatibility analysis, item 3). Empty means
    # /viz/panel-token (the minting endpoint) and /viz/panel itself both
    # return 503 rather than issuing/accepting an unsigned or weakly-signed
    # token — same "fail loud, never silently degrade" posture as
    # src/llm/chat.py's LlmNotConfiguredError.
    panel_token_secret: str = ""
    # ~1 year: the panel is a static, admin-configured iframe src with no
    # OAuth flow to refresh it (see README.md), so this deliberately isn't a
    # short-lived session token — revocation (bumping the stored version in
    # Redis, see panel_tokens.py) is the mechanism for invalidating one
    # early, not a short TTL forcing every embed to be re-minted constantly.
    panel_token_ttl_seconds: int = 60 * 60 * 24 * 365

    # ── App ───────────────────────────────────────────────────────────────────────
    log_level: str = "INFO"
    env: str = "development"

    # ── YAML config (loaded separately, merged at property access) ──────────────
    _yaml: dict = {}

    model_config = {"env_file": str(ROOT / ".env"), "extra": "ignore"}

    @model_validator(mode="after")
    def _validate_production_secrets(self) -> "Settings":
        """Fail fast if production is running with insecure defaults."""
        if self.env == "production" and self.neo4j_password == "scg_dev_local":
            raise ValueError(
                "neo4j_password must be changed from the default 'scg_dev_local' in production."
            )
        if self.env == "production" and not self.workspace_api_keys:
            raise ValueError(
                "workspace_api_keys must be configured in production (WORKSPACE_API_KEYS)."
            )
        if self.env == "production" and not self.panel_token_secret:
            raise ValueError(
                "panel_token_secret must be configured in production (PANEL_TOKEN_SECRET) "
                "for /viz/panel to mint or verify tokens."
            )
        return self

    def __init__(self, **data):
        super().__init__(**data)
        object.__setattr__(self, "_yaml", _load_yaml())

    # ── Accessors ─────────────────────────────────────────────────────────────────
    # Only sections the ported src/graph/*.py legacy modules actually read
    # (ontology_registry.load() -> settings.ontology; alias_registry.__init__ ->
    # settings.ingestion). Add more only once a phase's code actually calls
    # get_settings().<section>.
    @property
    def ontology(self) -> dict:
        return self._yaml.get("ontology", {})

    @property
    def ingestion(self) -> dict:
        return self._yaml.get("ingestion", {})


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
