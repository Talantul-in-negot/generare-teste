"""Concurrency on the governed write path and the shared counters.

What makes these tests worth writing
------------------------------------
Every guard here is only exercised when two callers collide, which in a test
suite means never — so a broken guard passes every existing test and fails only
in production, under load, as a lost update nobody can reproduce.

Why a fake store instead of `AsyncMock`
---------------------------------------
An `AsyncMock` returning a canned result cannot demonstrate a concurrency
property: it answers identically no matter what order the callers arrive in, so
"only one writer won" would be an assertion about the mock's script rather than
about the code. `_VersionedFindingStore` below holds real state and implements
the one Cypher semantic the guard depends on — a write that matches zero rows
performs no mutation.

What it does and does not prove: it proves the *application* honours the guard
(reads the version, refuses on mismatch, increments by exactly one, surfaces
StaleVersionError). It does **not** prove atomicity — that comes from Neo4j
executing the guarded statement as a single transaction, which only a live
integration test can show. The fake serialises each statement to model that
contract; if the real query were ever split into read-then-write, this fake
would still pass while production would race. That limitation is the reason
`docs/roadmap.md` still lists a live concurrency drill.

Verified non-vacuous by mutation: replacing the fake's guard with one that
always matches produces 8 winners and 8 work orders against a single finding,
and `test_only_one_of_many_concurrent_writers_wins` fails. A concurrency test
that passes for the wrong reason is worse than none, so that check is the
price of trusting these.
"""

from __future__ import annotations

import asyncio

import pytest

from graphrag.business.models import WorkOrder
from graphrag.business.repository import BusinessObjectRepository, StaleVersionError
from graphrag.core.tenant_quota import QuotaPolicy, TenantQuotaStore
from graphrag.core.token_revocation import TokenRevocationStore


class _VersionedFindingStore:
    """Minimal store implementing the guarded-write semantics of the real query.

    Only the two statements `create_work_order_from_finding` issues are
    understood; anything else raises so a silently-unhandled query cannot make
    a test pass by returning an empty list.
    """

    def __init__(self, *, status: str = "open", version: int = 1):
        self.finding = {"status": status, "object_version": version}
        self.work_orders: dict[str, dict] = {}
        self.transitions: list[dict] = []
        # One statement at a time: models Neo4j running the guarded statement
        # as a single transaction. Without this the fake would permit an
        # interleaving the database itself forbids, and the test would be
        # asserting a property the production system never has to satisfy.
        self._lock = asyncio.Lock()
        self.guarded_attempts = 0

    async def run(self, cypher: str, **params):
        async with self._lock:
            if "WHERE f.object_version = $expected_version" in cypher:
                return await self._guarded_write(params)
            if "RETURN f.status AS status, f.object_version AS version" in cypher:
                return [{
                    "status": self.finding["status"],
                    "version": self.finding["object_version"],
                }]
            raise AssertionError(f"unexpected statement in fake store: {cypher[:120]}")

    async def _guarded_write(self, params):
        self.guarded_attempts += 1
        # Yield once so concurrent callers genuinely interleave around the
        # guard rather than each running to completion in arrival order.
        await asyncio.sleep(0)
        matches = (
            self.finding["object_version"] == params["expected_version"]
            and self.finding["status"] in params["valid_sources"]
        )
        if not matches:
            # Zero rows matched -> the SET/CREATE clauses never ran. This is
            # the whole safety property of the guarded statement.
            return []
        from_state = self.finding["status"]
        self.finding["status"] = "remediating"
        self.finding["object_version"] = params["to_version"]
        self.transitions.append({
            "from_version": params["expected_version"],
            "to_version": params["to_version"],
        })
        work_order_id = params["wo_id"]
        self.work_orders.setdefault(work_order_id, dict(params["wo_props"]))
        return [{
            "finding_version": self.finding["object_version"],
            "finding_from_state": from_state,
            "work_order_id": work_order_id,
            "work_order_version": self.work_orders[work_order_id].get("object_version", 1),
        }]


def _work_order(index: int) -> WorkOrder:
    return WorkOrder(
        id=f"wo-{index}",
        tenant="aerospace",
        originating_finding_id="finding-1",
        title=f"Remediate {index}",
        reason_code="operator_request",
        created_by="operator-1",
        updated_by="operator-1",
    )


class TestOptimisticConcurrencyOnFindings:
    async def test_only_one_of_many_concurrent_writers_wins(self):
        store = _VersionedFindingStore(version=1)
        repository = BusinessObjectRepository(store)

        async def attempt(index: int):
            return await repository.create_work_order_from_finding(
                "aerospace",
                _work_order(index),
                expected_finding_version=1,
                actor_id="operator-1",
                reason_code="operator_request",
            )

        results = await asyncio.gather(
            *(attempt(i) for i in range(8)), return_exceptions=True,
        )
        succeeded = [r for r in results if not isinstance(r, Exception)]
        stale = [r for r in results if isinstance(r, StaleVersionError)]

        # Exactly one writer may transition version 1 -> 2. Anything else is a
        # lost update: two work orders against one finding, or a version that
        # skipped a value.
        assert len(succeeded) == 1
        assert len(stale) == 7
        assert store.finding["object_version"] == 2
        assert len(store.transitions) == 1

    async def test_version_increments_by_exactly_one(self):
        store = _VersionedFindingStore(version=5)
        repository = BusinessObjectRepository(store)
        await repository.create_work_order_from_finding(
            "aerospace",
            _work_order(1), expected_finding_version=5,
            actor_id="operator-1", reason_code="operator_request",
        )
        assert store.finding["object_version"] == 6
        assert store.transitions == [{"from_version": 5, "to_version": 6}]

    async def test_loser_learns_the_actual_version(self):
        # A caller that lost must be able to re-read and retry; a bare failure
        # with no actual version leaves it guessing.
        store = _VersionedFindingStore(version=3)
        repository = BusinessObjectRepository(store)
        with pytest.raises(StaleVersionError) as excinfo:
            await repository.create_work_order_from_finding(
                "aerospace",
                _work_order(1), expected_finding_version=1,
                actor_id="operator-1", reason_code="operator_request",
            )
        assert "3" in str(excinfo.value)

    async def test_a_losing_writer_creates_no_work_order(self):
        store = _VersionedFindingStore(version=1)
        repository = BusinessObjectRepository(store)

        async def attempt(index: int):
            return await repository.create_work_order_from_finding(
                "aerospace",
                _work_order(index), expected_finding_version=1,
                actor_id="operator-1", reason_code="operator_request",
            )

        await asyncio.gather(*(attempt(i) for i in range(5)), return_exceptions=True)
        # The dangerous partial state: a finding marked remediating with two
        # work orders, or a work order with no transition recorded.
        assert len(store.work_orders) == 1
        assert len(store.transitions) == 1

    async def test_sequential_writers_each_succeed_with_the_current_version(self):
        # Guards against the opposite failure: a guard so strict that a
        # correctly-ordered retry can never succeed.
        store = _VersionedFindingStore(version=1)
        repository = BusinessObjectRepository(store)
        await repository.create_work_order_from_finding(
            "aerospace",
            _work_order(1), expected_finding_version=1,
            actor_id="operator-1", reason_code="operator_request",
        )
        store.finding["status"] = "open"  # reopened by an operator
        await repository.create_work_order_from_finding(
            "aerospace",
            _work_order(2), expected_finding_version=2,
            actor_id="operator-1", reason_code="operator_request",
        )
        assert store.finding["object_version"] == 3
        assert len(store.transitions) == 2


class TestQuotaCountersUnderConcurrency:
    async def test_concurrent_consumption_loses_no_increments(self):
        # A read-modify-write implementation would drop increments here; the
        # symptom in production is a tenant that never reaches its ceiling.
        store = TenantQuotaStore(default_policy=QuotaPolicy(max_requests=10_000))
        await store.connect()
        await asyncio.gather(*(store.consume("acme", requests=1) for _ in range(200)))
        assert (await store.usage("acme"))["requests"]["used"] == 200

    async def test_concurrent_cost_accumulation_is_exact(self):
        store = TenantQuotaStore(default_policy=QuotaPolicy(max_cost_usd=10_000))
        await store.connect()
        await asyncio.gather(
            *(store.consume("acme", requests=0, cost_usd=0.01) for _ in range(100))
        )
        assert (await store.usage("acme"))["cost_usd"]["used"] == pytest.approx(1.0)

    async def test_concurrent_tenants_do_not_interfere(self):
        store = TenantQuotaStore(default_policy=QuotaPolicy(max_requests=10_000))
        await store.connect()
        await asyncio.gather(
            *(store.consume(f"tenant-{i % 4}", requests=1) for i in range(80))
        )
        for index in range(4):
            assert (await store.usage(f"tenant-{index}"))["requests"]["used"] == 20

    async def test_concurrent_singleton_cold_start_builds_one_store(self, monkeypatch):
        from graphrag.core import tenant_quota as tq

        monkeypatch.setattr(tq, "_store", None)
        monkeypatch.setattr(tq, "_store_lock", None)
        built = 0
        real_connect = TenantQuotaStore.connect

        async def counting_connect(self):
            nonlocal built
            built += 1
            await asyncio.sleep(0)
            await real_connect(self)

        monkeypatch.setattr(TenantQuotaStore, "connect", counting_connect)
        stores = await asyncio.gather(*(tq.get_quota_store() for _ in range(8)))
        assert built == 1
        assert len({id(store) for store in stores}) == 1


class TestRevocationUnderConcurrency:
    async def test_a_revocation_lands_even_under_concurrent_checks(self):
        store = TokenRevocationStore()
        await store.connect()
        claims = {"jti": "abc", "sub": "client-1", "iat": 1_000.0}

        async def check():
            return await store.is_revoked(claims)

        # Interleave the revocation with a burst of verifications, then assert
        # the terminal state: once revoked, it stays revoked.
        results = await asyncio.gather(
            *(check() for _ in range(20)),
            store.revoke_token("abc"),
            *(check() for _ in range(20)),
        )
        assert await store.is_revoked(claims) is True
        # Every individual check returned a bool, never raised or hung.
        assert all(isinstance(r, (bool, type(None))) for r in results)

    async def test_concurrent_revocations_of_the_same_token_are_idempotent(self):
        store = TokenRevocationStore()
        await store.connect()
        await asyncio.gather(*(store.revoke_token("abc") for _ in range(10)))
        assert await store.is_revoked({"jti": "abc"}) is True
        assert (await store.stats())["revoked_tokens"] == 1

    async def test_revoking_one_subject_does_not_affect_another(self):
        store = TokenRevocationStore()
        await store.connect()
        await asyncio.gather(
            store.revoke_subject("client-1"),
            store.revoke_subject("client-2"),
        )
        assert await store.is_revoked({"sub": "client-1", "iat": 1.0}) is True
        assert await store.is_revoked({"sub": "client-3", "iat": 1.0}) is False
