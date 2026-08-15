# From MCP Contract to Local Evidence: a Reproducible Walkthrough

This repository includes a bounded, repeatable local demonstration of the
platform's agent-facing path. It is deliberately useful without pretending that
a laptop run proves production scale or customer impact.

The walkthrough starts at the versioned MCP capability export. It then uses an
authenticated Streamable HTTP session to query a tenant-scoped graph, rather
than calling an in-process test double. Ten fixed graph-fact cases are
evaluated against the seeded `local-evidence` tenant and an explicit empty
corpus baseline.

The same local gateway receives concurrent graph-fact workloads. The checked-in
report records 100/100 and 1,000/1,000 successful requests at 32.90 and 26.24
requests per second. At concurrency 25, p95 reaches 36.27 seconds because each
request includes MCP session initialization; this exposes a useful optimization
target, not a capacity or availability claim.

The governed-write run follows a full evidence trail: an agent request is held
for approval; an authorized human approval releases it; the write executes once;
the same idempotency key returns the original receipt; a stale expected version
is refused; dry-run returns a preview; and compensation itself requires approval.
The harness uses a synthetic tenant and local development credentials only.

Reproduce the evidence with the commands in
[`../local-evidence-runbook.md`](../local-evidence-runbook.md). The resulting
JSON reports feed the public local evaluation report, while the ontology archive
contains a versioned manifest and SHA-256 checksums. A silent rendered walkthrough
is available at `../presentation/local-evidence-walkthrough.mp4`.

What this does not establish is equally important: production availability,
customer tenants, cost savings, prevented incidents, or actual manual-versus-
agent time reductions. The study protocol is included, but those outcomes require
observed users and independently captured measurements.
