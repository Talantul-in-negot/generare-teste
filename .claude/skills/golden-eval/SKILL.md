---
name: golden-eval
description: Run the aerospace golden-set regression eval and report the pass rate. Use when asked to check eval status, verify a fix didn't regress anything, or get the current pass/fail count.
---

# Golden Eval

Runs `evals/golden_set.json` against the live API and reports results.

## Steps

1. Confirm the local stack is up: `dev_neo4j`, `dev_redis`, `dev_rabbitmq` containers running,
   the API reachable at `GRAPHRAG_API_URL`, and `workers/query_worker.py` consuming the queue.
   If any of these are down, say so and stop — don't run the eval against a half-up stack.
2. Run:
   ```bash
   python scripts/run_golden_eval.py --tenant aerospace
   ```
3. Report verbatim: total pass rate, the by-type breakdown table, and the list of failing
   question IDs with their failure reason (from `evals/last_run.json`).
4. If the pass rate dropped versus the last recorded baseline, say so explicitly — don't bury a
   regression in a summary that only reports the current number.

Do not attempt to fix any failing question as part of this skill — it only reports. Fixing is a
separate, explicit decision.
