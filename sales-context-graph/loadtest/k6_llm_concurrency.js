// Phase 10, layer 3: LLM-call concurrency (docs/evaluation.md's B6).
//
// Repeats POST /api/v1/ask under increasing concurrency -- NOT against the
// real Anthropic/OpenAI API. docs/evaluation.md's B6 is explicit that this
// layer should run "against a stubbed/rate-limited target, not real API
// spend": running k6 concurrency levels against a real, billed LLM API
// would cost real money per request and conflate vendor/network variance
// with this system's own behavior. See loadtest/mock_llm_server.py and
// loadtest/README.md for how to point the API process at the mock
// (LLM_PROVIDER=anthropic, LLM_BASE_URL=http://localhost:4010).
//
// seller_id is supplied in the request body deliberately: AskRequest's
// seller_id is *caller context*, not something the LLM is asked to
// produce (src/usecases/nlq/ask.py's own docstring: "there is no Seller
// node in the graph... nothing to match a name against"), so supplying it
// here exercises the full classify -> resolve -> dispatch path against
// the mock's fixed "top-objections" classification, not just the
// isolated LLM round trip.
import http from "k6/http";
import { check } from "k6";
import { Counter } from "k6/metrics";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";
const WORKSPACE_ID = __ENV.WORKSPACE_ID || "ws-demo";
const API_KEY = __ENV.API_KEY;
if (!API_KEY) {
  throw new Error("set API_KEY to a real key from WORKSPACE_API_KEYS (see .env / loadtest/README.md)");
}
const SELLER_ID = __ENV.LLM_SELLER_ID || "loadtest-seller-1";

export const errors = new Counter("ask_errors");
export const llmUnconfigured = new Counter("ask_llm_unconfigured_503");

export const options = {
  // Deliberately lower VU counts than layers 1/2 -- LLM calls are the
  // slowest, most expensive step in this system's own request path even
  // against the mock's injected latency, and this layer's point is
  // observing concurrency behavior (queueing, tail latency, the mock's
  // simulated rate limit if MOCK_LLM_RATE_LIMIT_PCT is set), not raw
  // throughput.
  stages: [
    { duration: "15s", target: 3 },
    { duration: "30s", target: 3 },
    { duration: "15s", target: 8 },
    { duration: "30s", target: 8 },
    { duration: "10s", target: 0 },
  ],
  thresholds: {
    http_req_failed: ["rate<0.5"],
  },
};

export default function () {
  const body = JSON.stringify({
    question: "What are the most common objections across my pipeline?",
    seller_id: SELLER_ID,
    include_narrative: false,
  });

  const res = http.post(`${BASE_URL}/api/v1/ask`, body, {
    headers: {
      "Content-Type": "application/json",
      "X-Workspace-Id": WORKSPACE_ID,
      "X-Api-Key": API_KEY,
    },
    tags: { name: "ask" },
  });

  if (res.status === 503) {
    // Fails loud rather than a fabricated answer (src/llm/chat.py's own
    // design) -- counted separately from a hard failure so a
    // misconfigured run (LLM_PROVIDER not pointed at the mock) is obvious
    // in the report instead of blending into the generic error rate.
    llmUnconfigured.add(1);
    return;
  }

  const ok = check(res, {
    "status is 200": (r) => r.status === 200,
    "response has intent_id or ambiguities": (r) => {
      try {
        const parsed = JSON.parse(r.body);
        return typeof parsed.intent_id === "string" || Array.isArray(parsed.ambiguities);
      } catch {
        return false;
      }
    },
  });
  if (!ok) errors.add(1);
}
