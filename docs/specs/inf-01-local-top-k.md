# Spec: INF-01 recall gap — aerospace `local_top_k`

**Problem:** INF-01 ("Does FAA-AD-2024-01-02 supersede the 2020 directive...") fails because
the chunk containing the answer never reaches the top-10 pre-rerank fused candidate set —
confirmed by direct measurement (`docs/audit-2026-08-13.md`, "Retrieval rank diagnostic —
2026-08-14"). This is a fusion/recall gap upstream of reranking, not a cutoff problem.

**Change:**
1. Add `aerospace: { local_top_k: 15 }` under `retrieval.tenant_overrides` in
   `config/settings.yml`. Scoped to the aerospace tenant only — do not touch the global default
   (`local_top_k: 10`), which is the validated baseline for automotive/marketing (see the A124/A125
   revert history in the same file for why a global change here is the wrong move).
2. Re-run `python scripts/run_golden_eval.py --tenant aerospace`.

**Accept if:** INF-01 passes AND the aggregate pass rate does not drop below its pre-change value.
**Revert if:** INF-01 still fails, OR any previously-passing question regresses. Revert means
deleting the override line, not adjusting the number further — a second guess without a second
measurement is exactly the mistake this session already corrected once (TMP-03).

**Out of scope:** `rerank_top_k`, global defaults, any other tenant.

---

## Outcome — 2026-08-14: REVERTED

Implemented `aerospace: { local_top_k: 15 }`, restarted the API, and re-ran the golden eval.

**Process note first:** the first re-run reused 32/34 cached answers from before the config
change (`QueryCache` keys on `(query, tenant, context)`, not on retrieval config — it has no way
to know a knob changed). That run's 21/34 was not a valid test. Flushed the aerospace cache
(`get_query_cache().flush_tenant("aerospace")`) and re-ran cold before drawing any conclusion.
**Any future config-change verification via this script must flush the tenant's query cache
first, or the result is meaningless.**

**Cold-cache result:** 22/34 (64.7%), up from the 20/34 baseline in aggregate — but:
- INF-01 still failed, and its citation recall got *worse* (previously missing only the
  required wording; now missing all three AD citations).
- INF-02 still failed, same pattern (previously right citations/wrong wording; now missing
  citations too).
- **AUT-02 regressed** (was passing, now fails) and **NEG-02 regressed** (was passing, now
  fails) — two previously-passing questions broken by widening the candidate pool.
- Only PRE-02 flipped to passing.

Per this spec's own accept/revert rule (revert if INF-01 still fails OR anything previously
passing regresses) — both conditions are true, so **reverted**, despite the aggregate number
looking like an improvement. Config restored to `tenant_overrides: {}`, API restarted to match.

INF-01/INF-02 remain open with no working fix identified. A different `local_top_k` value is
not assumed safer without first understanding what specifically pulled AUT-02/NEG-02 off track —
that's a new diagnosis, not a retry of this one.
