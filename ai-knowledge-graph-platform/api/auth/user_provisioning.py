"""Explicit Google identity -> tenant provisioning table.

Problem solved
--------------
GET /auth/callback accepted ANY Google account and unconditionally issued a
token for settings.default_tenant -- there was no persistent mapping from a
Google identity to a tenant anywhere in the codebase. Every real browser
login landed in the same tenant regardless of who signed in, so production
multi-tenancy was never actually exercised by real users; only the dev-token
and M2M paths ever produced a non-default tenant.

This module is the missing mapping: an admin-scoped caller pre-provisions an
email address, then the first verified Google login binds that record to the
provider's immutable ``(issuer, sub)`` identity.  Subsequent logins resolve by
that identity rather than the mutable email address.  An unprovisioned account
is rejected at /auth/callback, not defaulted anywhere.

Same storage shape as the M2M client registry already shipped in
api/routes/auth.py (_client_get/_client_set): Redis hash when available, with
an in-memory fallback for non-Redis environments. Not factored into a shared
helper -- graphrag/monitoring/alerts.py has its own equivalent
_get_redis_sync() too, so this follows the existing (if imperfect) per-module
convention rather than refactoring unrelated code as a side effect of this
fix.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import structlog

log = structlog.get_logger(__name__)

_USERS_KEY = "graphrag:user_tenant_map"
_IDENTITIES_KEY = "graphrag:user_identity_map"
_users_mem: dict[str, dict] = {}   # fallback for non-Redis environments
_identities_mem: dict[str, str] = {}


class UserIdentityConflict(ValueError):
    """Raised when an email or provider subject is already bound elsewhere."""


def _get_redis_sync():
    """Return a sync Redis client for the provisioning table, or None."""
    try:
        import redis as redis_lib
        from graphrag.core.config import get_settings
        redis_url = get_settings().retrieval.get("redis_url", "")
        if not redis_url:
            return None
        return redis_lib.from_url(redis_url, socket_connect_timeout=1,
                                  socket_timeout=1, decode_responses=True)
    except (ImportError, OSError, ConnectionError, ValueError):
        return None


def _redis_error_types() -> tuple[type[BaseException], ...]:
    try:
        import redis as redis_lib
        return (redis_lib.exceptions.RedisError, OSError, ConnectionError, ValueError)
    except ImportError:
        return (OSError, ConnectionError, ValueError)


def _log_redis_fallback(operation: str, exc: BaseException) -> None:
    """Record that the provisioning table just diverged from shared storage.

    The in-memory fallback below is intentional, but silent divergence is not:
    without this, a Redis outage splits the identity->tenant map per replica
    and the only symptom an operator sees is a login that works on one pod and
    is rejected on the next.
    """
    log.warning(
        "user_provisioning.redis_unavailable",
        operation=operation,
        exception_type=type(exc).__name__,
        impact="falling back to per-process storage; records will not be shared across replicas",
    )


def normalize_email(email: str) -> str:
    """The provisioning key. Google emails are case-insensitive; storing and
    looking up with mixed case would make provisioning silently miss a match."""
    return email.strip().lower()


def _identity_key(issuer: str, subject: str) -> str:
    """Stable storage key for an OpenID Connect principal."""
    if not issuer or not subject:
        raise ValueError("issuer and subject are required")
    return f"{issuer}\x1f{subject}"


def get_user_record(email: str) -> dict | None:
    """Return the provisioning record for `email`, or None if unprovisioned."""
    key = normalize_email(email)
    r = _get_redis_sync()
    if r is not None:
        try:
            raw = r.hget(_USERS_KEY, key)
            return json.loads(raw) if raw else None
        except _redis_error_types() as exc:
            _log_redis_fallback("get_user_record", exc)
    return _users_mem.get(key)


def get_user_record_by_identity(issuer: str, subject: str) -> dict | None:
    """Return a provisioned user by its immutable OIDC issuer/subject pair."""
    identity = _identity_key(issuer, subject)
    r = _get_redis_sync()
    if r is not None:
        try:
            email_key = r.hget(_IDENTITIES_KEY, identity)
            return get_user_record(email_key) if email_key else None
        except _redis_error_types() as exc:
            _log_redis_fallback("get_user_record_by_identity", exc)
    email_key = _identities_mem.get(identity)
    return _users_mem.get(email_key) if email_key else None


def set_user_record(
    email: str, *, tenant: str, scopes: list[str], added_by: str,
) -> dict:
    """Provision (or re-provision) `email` for `tenant` with `scopes`.

    Returns the stored record. Caller is responsible for scope validation and
    the escalation guard (granted scopes must not exceed the provisioning
    admin's own) -- this module only persists what it's given.
    """
    key = normalize_email(email)
    # Re-provisioning within the same tenant changes scopes but must not erase
    # the immutable identity bound at the first verified login.
    existing = get_user_record(email)
    record = {
        "email": key,
        "tenant": tenant,
        "scopes": sorted(scopes),
        "added_by": added_by,
        "added_at": datetime.now(timezone.utc).isoformat(),
    }
    if existing:
        for field in ("issuer", "subject", "bound_at"):
            if existing.get(field):
                record[field] = existing[field]
    payload = json.dumps(record)
    r = _get_redis_sync()
    if r is not None:
        try:
            r.hset(_USERS_KEY, key, payload)
            return record
        except _redis_error_types() as exc:
            _log_redis_fallback("set_user_record", exc)
    _users_mem[key] = record
    return record


def bind_user_identity(email: str, *, issuer: str, subject: str) -> dict:
    """Bind a pre-provisioned email to one immutable OIDC identity.

    The bootstrap email is used exactly once, after the provider has asserted
    it is verified.  It cannot later be used to replace a bound subject, and a
    subject cannot be bound to a second provisioning record.
    """
    key = normalize_email(email)
    identity = _identity_key(issuer, subject)
    record = get_user_record(key)
    if record is None:
        raise KeyError("user is not provisioned")

    existing_issuer = record.get("issuer")
    existing_subject = record.get("subject")
    if existing_issuer or existing_subject:
        if (existing_issuer, existing_subject) != (issuer, subject):
            raise UserIdentityConflict("email is already bound to another identity")
        return record

    r = _get_redis_sync()
    if r is not None:
        try:
            mapped_email = r.hget(_IDENTITIES_KEY, identity)
            if mapped_email and mapped_email != key:
                raise UserIdentityConflict("identity is already bound to another user")
            bound = {
                **record,
                "issuer": issuer,
                "subject": subject,
                "bound_at": datetime.now(timezone.utc).isoformat(),
            }
            pipe = r.pipeline(transaction=True)
            pipe.hset(_USERS_KEY, key, json.dumps(bound))
            pipe.hset(_IDENTITIES_KEY, identity, key)
            pipe.execute()
            return bound
        except UserIdentityConflict:
            raise
        except _redis_error_types() as exc:
            _log_redis_fallback("bind_user_identity", exc)

    mapped_email = _identities_mem.get(identity)
    if mapped_email and mapped_email != key:
        raise UserIdentityConflict("identity is already bound to another user")
    bound = {
        **record,
        "issuer": issuer,
        "subject": subject,
        "bound_at": datetime.now(timezone.utc).isoformat(),
    }
    _users_mem[key] = bound
    _identities_mem[identity] = key
    return bound


def delete_user_record(email: str) -> bool:
    """Remove a provisioning record. Returns True if a record was removed."""
    key = normalize_email(email)
    identity = None
    if record := get_user_record(key):
        if record.get("issuer") and record.get("subject"):
            identity = _identity_key(record["issuer"], record["subject"])
    r = _get_redis_sync()
    if r is not None:
        try:
            pipe = r.pipeline(transaction=True)
            pipe.hdel(_USERS_KEY, key)
            if identity:
                pipe.hdel(_IDENTITIES_KEY, identity)
            result = pipe.execute()
            removed = result[0]
            return bool(removed)
        except _redis_error_types() as exc:
            _log_redis_fallback("delete_user_record", exc)
    removed = _users_mem.pop(key, None)
    if identity:
        _identities_mem.pop(identity, None)
    return removed is not None


def list_user_records(*, tenant: str) -> list[dict]:
    """Return every record provisioned for `tenant`.

    Filtered server-side after fetching rather than via a secondary Redis
    index -- this table is an admin allowlist, not a hot path, so an O(n)
    scan over all provisioned users is the right tradeoff for now. `tenant`
    is required and non-optional: this is exactly the kind of read that must
    never default to "everyone's", the same principle applied throughout
    F11-F13.
    """
    r = _get_redis_sync()
    if r is not None:
        try:
            raw_map = r.hgetall(_USERS_KEY)
            records = [json.loads(v) for v in raw_map.values()]
            return [rec for rec in records if rec.get("tenant") == tenant]
        except _redis_error_types() as exc:
            _log_redis_fallback("list_user_records", exc)
    return [rec for rec in _users_mem.values() if rec.get("tenant") == tenant]
