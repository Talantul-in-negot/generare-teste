# Lessons

Patterns learned the hard way in this repo. Review at session start; update
after any correction or self-caught mistake.

## L01 — Never trust a freshly-resolved dependency version without checking it against what's actually tested

`pip-compile` and a bare `pip install -e .` in a clean venv both independently
resolved `starlette==1.4.1`/`fastapi==0.141.1` — current on PyPI, but this
repo's test suite has only ever run against `starlette==0.50.0`/
`fastapi==0.128.2`. Both tools optimize for "resolves cleanly today," not
"matches what's tested." A lockfile generated without a constraints file
pinned to the actually-tested environment just launders "latest" through a
lockfile and calls it pinned — it isn't.

**How to apply:** when generating `requirements.lock.txt`, always pass
`pip install -e . -c <constraints-from-a-known-tested-environment>`, never a
bare install into an empty venv. See `requirements.lock.txt`'s own header
for the exact regenerate steps.

## L02 — A lockfile generated on one OS silently drops or mis-includes platform-conditional packages

Freezing on Windows: pulled in `pywin32` (a Windows-only transitive
dependency of `portalocker`, whose own metadata correctly marks it
`; sys_platform == "win32"` — `pip freeze` loses that marker) and silently
omitted `uvloop` (publishes no Windows wheels at all, but is a real
Linux/Mac performance extra of `uvicorn[standard]`). The first broke the
Linux Docker build outright; the second would have been a silent
performance regression in production with no error at all.

**How to apply:** after generating a lockfile on a different OS than the
deploy target, actually build the deploy image before trusting it. Don't
assume a `pip freeze` is portable across platforms — check it.

## L03 — A production Dockerfile that installs from floating `requirements.txt` ships whatever's newest on PyPI that day, not what's tested

Direct proof: building this repo's own `Dockerfile` from `requirements.txt`
resolved `starlette==1.4.1`/`fastapi==0.141.1` on a from-scratch build — the
exact untested versions from L01, reached completely independently via the
Docker build path. Having a correct lockfile sitting unused in the repo
doesn't help if the thing that actually ships doesn't read it.

**How to apply:** the Dockerfile installs from `requirements.lock.txt`, not
`requirements.txt`. `requirements.txt` stays as the abstract intent
`pyproject.toml` mirrors; only the lock file is ever installed from for a
build that will actually run.

## L04 — `pip install torch` on Linux defaults to the CUDA build even when the deploy target has no GPU

Building for `fly.toml`'s `shared-cpu-1x` (no GPU, 1GB memory) pulled several
GB of `nvidia-cu12-*` packages nobody asked for (cublas alone ~600MB, cudnn
~700MB) because `sentence-transformers` pulls in plain `torch`, and PyPI's
default `torch` wheel assumes CUDA. This turns a build that should take
~5 minutes into one that takes 15+ just downloading libraries that will
never be loaded.

**How to apply:** install `torch` explicitly first, pinned to the exact
locked version, from `--extra-index-url https://download.pytorch.org/whl/cpu`
— *before* the general `requirements.lock.txt` install, so pip finds it
already satisfied and never reaches for the GPU build. See `Dockerfile`.

## L05 — No `.dockerignore` means `COPY . .` ships `.git`, local dev-DB backups, and every cache directory

A from-scratch build's `chown -R appuser:appuser /app` step took 9+ minutes
on Windows/WSL2's virtualized filesystem because there was no
`.dockerignore` — `.git` history, `backups/` (30MB+ tarballs from
`scripts/backup_neo4j.sh`), `__pycache__`, and every lint/type cache were
all part of the build context and got copied and chowned for nothing.

**How to apply:** any repo with a Dockerfile needs a `.dockerignore` from
day one, not added reactively after a slow build. Check it exists and is
current whenever new large local-artifact directories (backups, caches,
generated reports) get added to the repo.

## L06 — A `docker-compose.yml` service pinned to `:latest` will drift underneath you during a long session, and the failure looks like a code bug

`qdrant/qdrant:latest` auto-updated from server 1.19.0 sometime during this
session while `qdrant-client==1.17.0` stayed pinned in the lockfile. The
client refused to talk to a server more than one minor version ahead —
surfaced as a genuine-looking `ResponseHandlingException` test failure with
no code change on either side. Cost real investigation time before the
version mismatch was found via `SHOW INDEXES`-style direct inspection
(`docker exec ... qdrant --version` equivalent).

**How to apply:** every service in `docker-compose.yml` should be pinned to
an explicit version, matching whatever client library version is locked —
never `:latest`, for exactly the reason floating dependency versions are a
problem everywhere else in this repo. When a test fails for a service
you didn't touch, check the running container's actual version before
assuming a code regression.

## L07 — Before declaring a test suite green or red, isolate failures under a quiet system before trusting the number

A full-suite run under heavy concurrent load (a multi-GB Docker build
running at the same time) produced 6 failures. Re-running each failing
test alone, one at a time, on a quiet system showed 5 of the 6 were pure
resource-contention flakes (including the single most security-critical
test in the repo, the vector tenant-isolation test) — only 1 was a real,
reproducible, pre-existing bug. Reporting "6 failures" without this step
would have been both alarming and wrong.

**How to apply:** never report a failing full-suite run as-is when
something heavy was running concurrently. Re-run each failure in isolation
before concluding anything is actually broken — and before concluding
nothing is, for a genuinely reproducible one.

## L08 — A background shell command that produces zero output for a long time may have silently died, not be "still working"

Multiple `run_in_background: true` Bash calls (a `pip install`, a
`docker build`) returned "completed" notifications with empty output files,
or sat with stale, unchanging output for many minutes past when real
activity should have produced *something*. The actual cause was never fully
diagnosed, but `nohup ... & disown` run explicitly in the foreground call
(rather than relying on the harness's own backgrounding) was reliable every
time it was tried as a fallback.

**How to apply:** if a backgrounded command's output file stays empty or
static for longer than the task should plausibly take, don't keep waiting
on faith — check for a live process (`ps -p <pid>`, or OS-level `tasklist`)
and consider it dead if nothing is running the log forward should be
independently checkable (e.g. an image existing, a container running). Fall
back to `nohup ... & disown` for anything that must survive the tool call
returning.

## L09 — Multiple sessions can share the same working directory without any coordination — check `git log` before assuming your working tree is yours alone

Mid-session, `git status` unexpectedly showed almost no pending changes and
`git log` showed 4-5 commits that were never made in this conversation,
already pushed to `origin`. Another process was editing and committing to
the exact same checkout concurrently (confirmed via diffs containing content
never written here). Nothing was lost — the other session's commits
happened to sweep up this session's uncommitted edits correctly — but it
could easily have gone the other way (a force-push, a conflicting edit to
the same file, a `git reset --hard`).

**How to apply:** before any git operation more consequential than `status`
or `diff` — especially before a "final commit" step — check `git log
--oneline -5` against what you expect the last known commit to be. If it
doesn't match, stop and investigate before committing or pushing; don't
assume the working tree is exclusively yours just because nothing told you
otherwise.

## L10 — A "filter after top-k" vector search is a cross-tenant leak the moment the index is populated

`src/resolution/candidates.py::vector_candidates()` passed `$limit` directly
as `db.index.vector.queryNodes`'s `numberOfNearestNeighbours`, computed
*before* the tenant `WHERE` filter — so one workspace's true match could be
starved out of the top-k by other tenants' higher-scoring rows, entirely
invisible once the workspace filter ran. It was latent for months because
the vector index was an unpopulated placeholder — found in the
Showpad-compatibility audit, fixed in Phase 1. The neighboring
`fulltext_candidates()` path right next to it in the same file already did
this correctly (filter, then limit), proving the two code paths had
silently drifted from the same intended pattern. Phase 7 explicitly refused
to populate the embedding index until this fix was re-verified live,
because doing so first would have turned a latent bug into an active leak.

**How to apply:** whenever a query mixes an approximate/ranked index
(vector, full-text) with a tenant/security filter, the filter must be
applied *before or during* the ranked search, never after a top-k
truncation — write a test that constructs the actual crowding-out scenario
(many higher-scoring other-tenant rows) and revert the fix once to confirm
it fails, not just that it passes.

## L11 — Credentials in a URL query string are a live vulnerability even in a "just for embedding" internal panel

`GET /viz/panel` took `workspace_id`, `api_key`, and `opportunity_id` as raw
`URLSearchParams` — the real workspace API key sat in browser history,
referrer headers, and server access logs, with zero server-side auth on the
route itself. Replaced with a short-lived, HMAC-signed, Redis-backed,
independently revocable panel token minted via a separate authenticated
endpoint.

**How to apply:** never accept a long-lived secret as a URL query parameter
on a GET route meant to be embedded (iframe, shareable link) — mint a
scoped, revocable token from an authenticated endpoint instead, even for
"internal demo" surfaces.

## L12 — Truncating a candidate pool without ordering by relevance silently drops the correct answer for entities created later

`CandidateGenerator.all_names_in_workspace` had no `ORDER BY`, and
`union_candidates()` truncated by Python dict insertion order, which
tracked Neo4j's unordered `MATCH` return order. A synthetic-600-entity eval
(`tests/eval/test_blocking_recall_at_scale.py`) measured
`blocking_recall@50 = 0.40` overall but `0.00` for any entity created at or
past the pool's midpoint — a real correctness bug (a present, correct match
silently dropped before scoring ever ran), not a "not stress-tested"
caveat as earlier docs had inaccurately described it. Fixed by sorting the
merged pool by lexical similarity to the actual mention text before
truncating, which the caller already had available and simply wasn't
passing through — recall went to 1.00 for every target regardless of
creation position.

**How to apply:** any code that truncates a "top N" candidate/result set
must sort by relevance to the actual query first — never by incidental DB
return order or insertion order. Write an eval that specifically varies
creation/insertion order of the correct answer, not just its presence in a
small fixture.

## L13 — A "durable" queue that deletes-on-claim (`BLPOP`) loses jobs outright on a worker crash, not just delays them

The original Redis ingestion queue used `BLPOP`, which atomically removes a
job from the queue the instant a worker claims it. If that worker then
crashed before finishing, the job was neither on the queue nor in the
dead-letter queue — gone, with no error, no trace, and no way to know it
had ever existed. Fixed by switching to `BLMOVE ... LEFT LEFT`, which
atomically moves the job into a per-worker "processing" list instead of
deleting it, plus a claim timestamp; a reaper on every poll loop puts
anything past a visibility timeout back through the same bounded
retry/dead-letter path an ordinary failure uses.

**How to apply:** "durable queue" claims need to be checked against the
actual claim/ack semantics of the underlying primitive — a queue is not
durable against worker crashes unless claimed-but-unfinished work is
visible somewhere and has a timeout-based recovery path, not just
persisted-until-claimed.

## L14 — CPython can reuse `id()` across a closed event loop and a freshly created one, silently breaking "same loop" checks

`src/core/redis_client.py`'s loop-affinity check compared `id(loop)` to
decide whether to reuse a cached Redis client — but CPython can and does
reuse the integer id of a garbage-collected, closed event loop for a
brand-new one in the same process, so the check passed when it shouldn't
have, reusing a dead client's transport and failing with `'NoneType' object
has no attribute 'send'`, only surfaced by a full-suite run, not a
per-file one. Fixed by comparing the loop object itself via a `weakref`
instead of its `id()`.

**How to apply:** never use `id(obj)` as a cheap identity/liveness check
across object lifetimes that might involve garbage collection (event
loops, closed connections) — hold a `weakref` or the object itself, since
`id()` reuse after GC is a real, not theoretical, failure mode in CPython.

## L15 — Resolving a global singleton once at module-import time silently freezes it before the real one exists

`api/routes/ingestions.py`'s `_store` was resolved once at module import
time, capturing whichever Redis client existed at that moment — a fine
pattern in a script, but wrong in a long-lived server process where the
real client can come up after import. Fixed with a `_StoreProxy` that
re-resolves `get_ingestion_store()` fresh on every request.

**How to apply:** any module-level "resolve the current singleton"
assignment in a long-lived server process should be replaced by a
proxy/accessor that re-resolves per use, not a value captured once at
import time — this class of bug only shows up under specific
startup-ordering conditions, so it won't be caught by a quick smoke test.

## L16 — "Implement literally everything, including the items you correctly rejected" is a legitimate instruction, and rejection reasoning should be preserved, not deleted, when later overridden

Several ADRs (Kafka transport, Qdrant secondary store, LLM gateway
fallback, prompt-injection guardrail) were originally analyzed and
explicitly rejected as "wrong for this system now" in an external-brief
cross-check, then built anyway per a reaffirmed stakeholder instruction to
implement the full document including previously-rejected items. Each ADR
restates the original rejection reasoning verbatim before explaining what
shipped and why the stated risk doesn't land for the disabled-by-default
version actually built (e.g. the guardrail's original objection — "a
probabilistic classifier over a deterministically-handled problem" — is
still true, so it shipped `log_only` by default rather than `block`).

**How to apply:** when a stakeholder overrides a documented "we rejected
this" decision, don't delete or rewrite the original analysis — append what
changed and why the original objection is now mitigated (e.g.
feature-flagged off by default), so the record shows both that the
objection was real and why it stopped being blocking.

## L17 — An "improvement" applied to the wrong layer of the pipeline can silently disturb code that was already correct

The original plan called for a reranker inside `ContextGraphBuilder`'s
Claim scoring, but that builder had no free-text query to rerank against —
the codebase's actual dense+BM25 hybrid pipeline (`src/resolution/scoring.py`)
already had a measured calibration (`DEFAULT_LEXICAL_WEIGHT = 0.97`)
showing general-purpose embeddings are the weaker signal for short
proper-noun matching. Bolting a cross-encoder onto that system risked
disturbing something already correct, not fixing a gap. Built instead as an
optional query-text-aware rerank step on Claims, gated by a new flag,
defaulting off. The same reasoning recurred later: an optional Qdrant
vector backend was deliberately *not* wired into `CandidateGenerator` — the
exact file that had just had a real cross-tenant leak fixed in it — because
routing an optional capability through security-critical, recently-fixed
code carried real risk for no measured benefit.

**How to apply:** before wiring a new optional capability into an existing
pipeline stage, check whether that stage already has a measured, working
calibration for the thing you're about to touch — if so, land the new
capability as an additive, off-by-default path elsewhere rather than
modifying code that's already correct and tested.

## L18 — A brief's own recommended threshold, followed literally, would have rejected the exact positive case it was meant to catch

`docs/plan.md` suggested an entity-resolution `base_threshold` of 0.75; the
actual shipped value is 0.70, with an in-code comment noting that 0.75
would reject the real "Volks Wagen" → "Volkswagen Group" match this system
was built to catch, documented via live-verified scores in the end-to-end
walkthrough. The plan's number was a reasonable prior, not a measured one.

**How to apply:** treat threshold values in design docs/plans as starting
hypotheses, not settled constants — verify them against a real positive
example the system is actually meant to resolve before shipping the plan's
suggested number as-is.

## L19 — A repository-wide MERGE-key convention can have silent exceptions that only show up as a cross-tenant write, not a read bug

Three `MERGE` clauses in `conversation_repository.py` (`TranscriptSegment`,
`Participant`, `SpeakerResolution`) omitted `workspace_id` from their merge
key, unlike every other repository in the codebase — found only via testing
during the product-completeness pass, not by inspection or convention
review. Because the leak was in a *write*-path merge key, not a read-path
filter, it wouldn't show up as a wrong query result; it would show up as
data from one workspace silently merging into another workspace's node.

**How to apply:** when a codebase has an established multi-tenant
convention (e.g. "every MERGE key includes workspace_id"), audit every
repository file against that convention explicitly and mechanically (grep
for MERGE clauses missing the field) rather than assuming consistency —
this class of bug is invisible in code review of any single file and only
surfaces via targeted testing.

## L20 — Reusing a security-critical test fixture across repeated local runs, without cleanup, can silently pollute the exact vector query it's meant to protect

Repeated local runs of `tests/integration/test_embedding_backfill.py` left
240 leftover embedded Contacts in the shared dev Neo4j instance, crowding
out a later run's own vector query results — the same class of cross-run
pollution risk that the tenant-isolation test had already had to guard
against, recurring in a different test file because the cleanup pattern
wasn't applied uniformly.

**How to apply:** any integration test that writes real rows against a
shared, persistent local dev database needs explicit teardown, especially
for tests near tenant-isolation or vector-search correctness — don't assume
a clean environment; a "passing" test against a polluted database can hide
the exact bug it's supposed to catch.

## L21 — A metrics/observability plan can be one-third built and look mostly done, unless you literally grep for the dependency

An external architecture brief assumed OpenTelemetry instrumentation was
already in place per the plan's named nine metrics. Direct verification
found `opentelemetry` appeared nowhere in the codebase, was absent from
`pyproject.toml`, and there was no `/metrics` endpoint or any metric of any
kind — `structlog` alone (used in 16 modules) had created the impression of
more observability coverage than actually existed. Every operational number
cited elsewhere in the project's own docs at that point therefore came from
a one-off test run, never a running system.

**How to apply:** when auditing whether an observability/dependency plan is
implemented, grep for the actual package name and concrete endpoint, don't
infer completion from adjacent-but-different signals (e.g. structured
logging existing does not imply metrics or tracing exist).

## L22 — Voice output must never become the critical path for a sales answer

The optional TTS path initially had a generous ten-second provider timeout.
That was technically resilient but poor product behavior: a seller could wait
far beyond the acceptable conversational threshold for audio even though the
grounded text answer was already available. The Ask surface now renders text
first, requests audio separately, uses a two-second default timeout, and keeps
the text answer when the provider is unavailable or slow.

**How to apply:** treat audio as an enhancement, not an answer dependency.
Measure time-to-first-text independently from time-to-first-audio, keep the
provider key server-side, use bounded input sizes, and make fallback visible
without inventing audio or delaying the evidence-backed response.

## L23 — A presentation artifact is part of the product surface and must be regenerated after UX changes

The demo film was initially correct for the Ask flow but became stale when
quick questions changed from a selector to direct numbered actions. Updating
the UI without regenerating the MP4 would have left the sales story and the
actual interaction out of sync. The renderer now presents the same 1–4 mapping,
direct click behavior, text-first response, and TTS fallback used by the live
surface; the final MP4 is checked for codec, resolution, duration, and a visual
frame before handoff.

**How to apply:** whenever a showcased interaction changes, update the source
storyboard and re-render the artifact. Validate the artifact itself (not only
the renderer), track the final deliverable, and keep intermediate frames/audio
out of version control.

## Project-start retrospective

The following lessons capture the progression from the first RAG experiments
to the current Showpad-shaped sales companion. They are intentionally ordered
by the project stages, so a new contributor can understand why the current
architecture looks more deliberate than the initial prototype.

## L24 — Start with a measurable user question, not with a graph schema

The early project work explored generic chat, document Q&A, RAG and agent
patterns before the sales use case was fixed. Those experiments were useful for
learning, but they did not define what a seller must decide next. The project
became coherent only after the target questions were explicit: objections,
stakeholders, content recommendation, deal change and evidence provenance.

**How to apply:** write the representative questions and acceptance criteria
before choosing Neo4j, embeddings, agents or an LLM. Every later component must
be traceable to a question a seller actually asks.

## L25 — A knowledge graph is valuable when it preserves evidence, time and scope

Moving from generic RAG to a sales Context Graph exposed the missing fields in
plain chunks: source identity, timestamps, polarity, confidence, workspace
boundaries and supersession. A graph without those properties is only a more
complicated index and cannot support a trustworthy answer or an as-of query.

**How to apply:** model claims and evidence as first-class objects. Treat
provenance, tenant identity and temporal validity as required data, not optional
metadata added after the retrieval layer is complete.

## L26 — Resolve messy names before retrieval and keep ambiguity visible

Real transcripts contain variants such as “Volks Wagen”, abbreviations and
partial names. The project learned that retrieval quality depends on bounded
candidate generation, measured scoring and an explicit unresolved state; a
silent best-effort link is worse than asking the seller to clarify.

**How to apply:** preserve candidate scores and reasons, test both positive and
ambiguous examples, and make the UI/API return clarification requirements
instead of guessing across opportunities or tenants.

## L27 — Optimize the whole retrieval path, not a single search algorithm

The system evolved from basic retrieval to BM25/vector fusion, reranking,
context-graph construction, caching and query-specific budgets. Each layer has
different strengths: lexical matching handles proper names, vectors help
semantic recall, and the graph constrains the final evidence set.

**How to apply:** measure recall, grounding, latency and token cost per stage.
Do not replace a calibrated stage with a fashionable model without a regression
benchmark and a rollback path.

## L28 — Production readiness is a sequence of failure-mode closures

The later phases addressed failures that a happy-path demo could not expose:
crashed ingestion workers, Redis claim recovery, N+1 queries, cross-tenant
reads and writes, PII egress, prompt injection, rate limits, backups, erasure,
audit events, readiness checks and load behavior.

**How to apply:** maintain a risk register and close one concrete failure mode
at a time with a regression test. “The endpoint returns 200” is not evidence of
durability, isolation or recoverability.

## L29 — Feature flags need an operational contract, not just a Boolean

Kafka, Qdrant, reranking, SSO, gateway fallback and other capabilities were
introduced as optional paths. The useful pattern was to document defaults,
required dependencies, failure behavior and production enablement conditions;
otherwise a flag only hides an untested branch.

**How to apply:** for every feature flag, document who enables it, what health
signal proves it is working, what happens when the dependency is down, and how
to disable it without a data migration.

## L30 — Demo credibility comes from showing boundaries as clearly as capabilities

The Showpad presentation became stronger when it showed both the working
companion behavior and the remaining connector boundary: the project is
Showpad-shaped, but it is not a packaged OAuth/AppExchange integration. The
same principle applies to local versus cloud latency and to optional TTS.

**How to apply:** label measured local results, cloud assumptions and external
integration work explicitly. A precise boundary builds more trust than a demo
that implies capabilities it cannot yet verify.
