---
description: Run the golden-set regression eval against the aerospace tenant and report the pass rate.
---

Run this command and report its output verbatim (pass/fail count, by-type breakdown, any failing IDs):

```bash
python scripts/run_golden_eval.py --tenant aerospace
```

Requires the local stack running (Neo4j/Redis/RabbitMQ via `docker compose -f compose.dev.yaml up -d`,
API on `GRAPHRAG_API_URL`, and `query_worker.py` consuming the queue). If the run fails to connect,
say so plainly rather than retrying blindly — check `docker ps` and the API health endpoint first.
