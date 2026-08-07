# Load testing (Phase 10)

Implements `docs/evaluation.md`'s **B6. Concurrency load testing**: k6
across the three layers the source brief asked for — ingestion throughput,
Context Graph retrieval latency, LLM-call concurrency — run separately, the
way the brief's own structure recommended.

**This is a baseline generator, not a benchmark against someone else's
SLOs.** The brief's specific targets (p95 < 100 ms retrieval at 5,000 RPS,
TTFT < 1.2 s) are vendor-scale numbers with no measured basis on this
system and are **not** adopted here as pass/fail thresholds. The only
thing `make loadtest` produces is a repeatable, dated report of what this
system actually does on the machine it's run on — re-run it after a change
and diff the two reports.

## Prerequisites

- Docker (for the [`grafana/k6`](https://hub.docker.com/r/grafana/k6) image
  — no local k6 install needed; nothing in this repo depends on k6 being on
  `PATH`).
- Neo4j + Redis running: `make up` (the `loadtest` Makefile target already
  depends on this).
- A real API key: `API_KEY=<value>` from `WORKSPACE_API_KEYS` in your
  `.env`, for whichever `WORKSPACE_ID` you pass (default `ws-demo`).

## Running

```bash
API_KEY=<your-key> make loadtest
```

or directly:

```bash
WORKSPACE_ID=ws-demo API_KEY=<your-key> bash loadtest/run_baseline.sh
```

This starts a **dedicated** API process on its own port (default `8099`)
— not the shared `docker compose` `api` service — so a load-test run never
restarts or reconfigures infrastructure other work in this repo might be
using. Neo4j and Redis are still the real, shared services; only the LLM
call (layer 3) is pointed at a local mock.

Results land in `loadtest/results/<UTC timestamp>/`: one log per layer plus
a `summary.txt` pulling out `http_req_duration` / `http_req_failed` /
`iterations` / `checks` from each. That directory is gitignored — these are
dated run artifacts, not source.

## The three layers

1. **`k6_ingestion_throughput.js`** — repeats `POST
   /api/v1/ingestions/crm` with a unique Account per iteration.
   `docs/evaluation.md`'s B6 names this layer's real, previously-untested
   failure mode explicitly: "the single serial worker plus
   `blpop`-without-visibility-timeout." Phase 4 fixed that gap
   (`docs/adr-0001`'s addendum); this is what actually exercises the queue
   under concurrent load to see the fix hold.
2. **`k6_context_retrieval.js`** — repeats `POST /api/v1/context/build`,
   the same operation the "Load/latency — now measured once, honestly, not
   a load test" note in `docs/evaluation.md` said still needed a real
   concurrent run. Pass `CONTEXT_SUBJECT_ID=<id>` (a subject with real
   Claims — e.g. from `make demo`) for a non-trivial graph; unscoped still
   measures real latency against whatever's been ingested.
3. **`k6_llm_concurrency.js`** — repeats `POST /api/v1/ask` against
   **`mock_llm_server.py`**, not the real Anthropic/OpenAI API.
   `docs/evaluation.md`'s B6 is explicit that this layer should run
   "against a stubbed/rate-limited target, not real API spend" — running
   k6 concurrency levels against a real, billed LLM API would cost real
   money per request and conflate vendor/network variance with this
   system's own behavior. The mock (stdlib `http.server`, no new
   dependency) mimics Anthropic's Messages API shape closely enough for
   the real `anthropic` SDK client to talk to it
   (`src/llm/chat.py::build_chat_fn(base_url=...)`,
   `src/core/config.py`'s `LLM_BASE_URL`), injects a configurable
   artificial latency (`MOCK_LLM_LATENCY_MS`, default 400 ms), and can
   optionally simulate rate limiting (`MOCK_LLM_RATE_LIMIT_PCT`, default
   0) to observe how this system behaves against a degraded LLM backend
   without a real outage.

## Running a layer standalone

Each script also runs on its own against any already-running API instance.
k6 runs *inside* a container, so `BASE_URL` needs `host.docker.internal`
(Docker Desktop's host-reachable hostname on Windows/Mac; the
`--add-host` flag below wires the same hostname up on Linux too) rather
than `localhost` — verified directly: Docker Desktop's `--network host`
does **not** reliably reach the host on Windows/Mac (tested: connection
refused), which is why `run_baseline.sh` doesn't use it either.

```bash
docker run --rm --add-host=host.docker.internal:host-gateway \
  -e BASE_URL=http://host.docker.internal:8000 -e WORKSPACE_ID=ws-demo -e API_KEY=<your-key> \
  -v "$(pwd)/loadtest:/scripts" grafana/k6 run /scripts/k6_ingestion_throughput.js
```

For layer 3 standalone, the target API process must itself be started with
`LLM_PROVIDER=anthropic LLM_API_KEY=<any-non-empty-value>
LLM_BASE_URL=http://localhost:4010` (or wherever `mock_llm_server.py` is
listening) — `run_baseline.sh` does this for you; running the layer 3
script against an API process that isn't configured this way will either
502/503 (LLM not pointed at the mock) or spend real API calls (a real
`LLM_API_KEY` configured against the real vendor) — check which is running
before pointing this script at it.

## What's deliberately not built here

No CI wiring (a load test's numbers are only meaningful relative to the
machine and moment they were run on — publishing them as a gate would
invite exactly the "vendor-scale numbers with no measured basis" problem
this phase exists to avoid). No distributed k6 execution (a single-machine
run matches this repo's own "vertical slice, not a product" framing
elsewhere in `docs/evaluation.md`). No automatic regression detection
between runs — reading two `summary.txt` files side by side is the
mechanism, deliberately, until a real need for more shows up.
