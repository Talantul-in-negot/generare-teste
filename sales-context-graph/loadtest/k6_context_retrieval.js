// Phase 10, layer 2: Context Graph retrieval latency (docs/evaluation.md's
// B6). Repeats POST /api/v1/context/build -- ContextGraphBuilder.build(),
// the same operation src/context_graph/builder.py's single-threaded,
// single-machine, 300-Claims measurement (docs/evaluation.md's "Load/
// latency -- now measured once, honestly, not a load test" note) explicitly
// said still needed a real concurrent run to become a load test rather
// than a latency snapshot. This is that run.
//
// CONTEXT_SUBJECT_ID (optional) scopes the build to one subject with real
// Claims -- run `make demo` (demo_volkswagen.py) first and pass that
// demo's subject id for a non-trivial graph; unscoped (the default) still
// measures real latency, just against a workspace-wide or empty graph
// depending on what's been ingested. Either is a legitimate baseline --
// the report should say which mode it ran in.
import http from "k6/http";
import { check } from "k6";
import { Counter } from "k6/metrics";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";
const WORKSPACE_ID = __ENV.WORKSPACE_ID || "ws-demo";
const API_KEY = __ENV.API_KEY;
if (!API_KEY) {
  throw new Error("set API_KEY to a real key from WORKSPACE_API_KEYS (see .env / loadtest/README.md)");
}
const SUBJECT_ID = __ENV.CONTEXT_SUBJECT_ID || "";

export const errors = new Counter("context_build_errors");

export const options = {
  stages: [
    { duration: "15s", target: 10 },
    { duration: "40s", target: 10 },
    { duration: "15s", target: 30 },
    { duration: "40s", target: 30 },
    { duration: "10s", target: 0 },
  ],
  thresholds: {
    http_req_failed: ["rate<0.1"],
  },
};

export default function () {
  const body = {};
  if (SUBJECT_ID) body.subject_id = SUBJECT_ID;

  const res = http.post(`${BASE_URL}/api/v1/context/build`, JSON.stringify(body), {
    headers: {
      "Content-Type": "application/json",
      "X-Workspace-Id": WORKSPACE_ID,
      "X-Api-Key": API_KEY,
    },
    tags: { name: "context_build" },
  });

  const ok = check(res, {
    "status is 200": (r) => r.status === 200,
    "response has claims array": (r) => {
      try {
        return Array.isArray(JSON.parse(r.body).claims);
      } catch {
        return false;
      }
    },
  });
  if (!ok) errors.add(1);
}
