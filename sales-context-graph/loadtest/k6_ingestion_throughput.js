// Phase 10, layer 1: ingestion throughput (docs/evaluation.md's B6).
//
// Each iteration POSTs one small, unique CRM batch (one Account) to
// POST /api/v1/ingestions/crm and waits for the synchronous response --
// this repo's MVP ingestion path runs in-process within the request
// (api/routes/ingestions.py's own docstring), so request latency here
// *is* ingestion latency, not just an enqueue acknowledgement, unless
// INGESTION_QUEUE_ENABLED=true is set on the server (in which case this
// measures enqueue latency instead -- either is a legitimate thing to
// baseline, just note which mode the server was running in when reading
// the report).
//
// docs/evaluation.md's B6 names this layer's real, previously-untested
// failure mode explicitly: "the single serial worker plus
// blpop-without-visibility-timeout" -- Phase 4 fixed the visibility-
// timeout gap; this script is what actually exercises the queue under
// concurrent load to see the fix hold.
import http from "k6/http";
import { check } from "k6";
import { Counter } from "k6/metrics";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";
const WORKSPACE_ID = __ENV.WORKSPACE_ID || "ws-demo";
const API_KEY = __ENV.API_KEY;
if (!API_KEY) {
  throw new Error("set API_KEY to a real key from WORKSPACE_API_KEYS (see .env / loadtest/README.md)");
}

export const errors = new Counter("ingestion_errors");

export const options = {
  // Explicitly NOT the source brief's vendor-scale numbers (5,000 RPS) --
  // docs/evaluation.md's B6 says adopting those would be "copying numbers
  // with no measured basis in this system." These stages are a modest,
  // repeatable local-machine ramp; the report is the artifact, not a
  // pass/fail threshold against someone else's SLO.
  stages: [
    { duration: "15s", target: 5 },
    { duration: "30s", target: 5 },
    { duration: "15s", target: 15 },
    { duration: "30s", target: 15 },
    { duration: "10s", target: 0 },
  ],
  thresholds: {
    // A floor, not a target: fail loud if something is badly broken
    // (e.g. every request 500ing), not a claim about what "good" is.
    http_req_failed: ["rate<0.5"],
  },
};

export default function () {
  const uniqueId = `loadtest-acc-${__VU}-${__ITER}-${Date.now()}`;
  const body = JSON.stringify({
    accounts: [{
      Id: uniqueId,
      Name: `Load Test Account ${uniqueId}`,
      Website: "loadtest.example",
      IsDeleted: false,
      MasterRecordId: null,
    }],
  });

  const res = http.post(`${BASE_URL}/api/v1/ingestions/crm`, body, {
    headers: {
      "Content-Type": "application/json",
      "X-Workspace-Id": WORKSPACE_ID,
      "X-Api-Key": API_KEY,
    },
    tags: { name: "ingest_crm" },
  });

  // NOTE: this route always answers 202 (api/routes/ingestions.py's own
  // docstring: the API returns an ingestion id, not a held-open request) --
  // even when the pipeline itself failed, the response is still 202 with
  // state=FAILED_PERMANENT/FAILED_RETRYABLE. A check on status/shape alone
  // would silently pass a systemic failure (e.g. Neo4j unreachable) as a
  // "known state" -- caught during this phase's own verification, when a
  // wrong NEO4J_URI default made every request fail server-side while this
  // check kept reporting 100% success. state must be one of the two
  // *successful* in-flight states to count as a pass; ACCEPTED/PERSISTING
  // are here too (still-in-progress once an async queue transport is
  // enabled) but a FAILED_* state always counts as a real failure now.
  const ok = check(res, {
    "status is 202": (r) => r.status === 202,
    "state indicates success or in-progress, not failure": (r) => {
      try {
        const parsed = JSON.parse(r.body);
        return ["ACCEPTED", "PERSISTING", "COMPLETED"].includes(parsed.state);
      } catch {
        return false;
      }
    },
  });
  if (!ok) errors.add(1);
}
