#!/usr/bin/env bash
# Phase 10 (docs/evaluation.md's B6) -- orchestrates all 3 load-test layers
# and writes one timestamped baseline report. Run via `make loadtest`, or
# directly: WORKSPACE_ID=ws-demo API_KEY=<real-key> loadtest/run_baseline.sh
#
# Explicitly does NOT adopt the source brief's vendor-scale numbers (p95 <
# 100ms retrieval at 5,000 RPS, TTFT < 1.2s) as thresholds or a pass/fail
# gate -- docs/evaluation.md's B6 says plainly there is no measured basis
# for those numbers on this system. This script's only job is a repeatable,
# dated baseline report; the run itself is the artifact.
#
# Runs a *dedicated* API process on its own port (default 8099), started
# fresh by this script with LLM_PROVIDER/LLM_BASE_URL pointed at
# loadtest/mock_llm_server.py -- deliberately not the shared docker-compose
# `api` service other work in this repo may depend on, so a load-test run
# never restarts or reconfigures infrastructure someone else is using.
# Neo4j and Redis are still the real, shared services (`make up`) -- only
# the LLM call is mocked.
set -euo pipefail
cd "$(dirname "$0")/.."

WORKSPACE_ID="${WORKSPACE_ID:-ws-demo}"
API_KEY="${API_KEY:?set API_KEY to a real key from WORKSPACE_API_KEYS -- see .env / loadtest/README.md}"
API_PORT="${LOADTEST_API_PORT:-8099}"
# The api process itself listens on localhost (below); k6 runs *inside* a
# container, where "localhost" means the container, not this host. Docker
# Desktop's --network host doesn't reliably reach the host on Windows/Mac
# (verified: connection refused), so k6 talks to the host via
# host.docker.internal instead -- see run_layer()'s `--add-host` below,
# which also makes this work on Linux Docker (20.10+) where
# host.docker.internal isn't wired in by default.
BASE_URL_FOR_API="http://localhost:${API_PORT}"
BASE_URL="http://host.docker.internal:${API_PORT}"
MOCK_LLM_PORT="${MOCK_LLM_PORT:-4010}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RESULTS_DIR="loadtest/results/${TIMESTAMP}"
mkdir -p "$RESULTS_DIR"

echo "== Phase 10 load-test baseline -- ${TIMESTAMP} =="
echo "results directory: ${RESULTS_DIR}"

echo "-- ensuring neo4j + redis are up (docker compose up -d neo4j redis) --"
docker compose up -d neo4j redis

echo "-- starting mock LLM server on :${MOCK_LLM_PORT} (loadtest/mock_llm_server.py) --"
MOCK_LLM_PORT="$MOCK_LLM_PORT" python loadtest/mock_llm_server.py > "${RESULTS_DIR}/mock_llm_server.log" 2>&1 &
MOCK_LLM_PID=$!

echo "-- starting a dedicated api process on :${API_PORT}, pointed at the mock LLM --"
env \
  NEO4J_URI="${NEO4J_URI:-bolt://localhost:7688}" \
  REDIS_URL="${REDIS_URL:-}" \
  WORKSPACE_API_KEYS="{\"${WORKSPACE_ID}\":\"${API_KEY}\"}" \
  LLM_PROVIDER=anthropic \
  LLM_API_KEY="loadtest-mock-key-not-real" \
  LLM_BASE_URL="http://localhost:${MOCK_LLM_PORT}" \
  python -m uvicorn api.main:app --port "$API_PORT" > "${RESULTS_DIR}/api.log" 2>&1 &
API_PID=$!

cleanup() {
  echo "-- stopping the dedicated api process and mock LLM server --"
  kill "$API_PID" "$MOCK_LLM_PID" 2>/dev/null || true
  wait "$API_PID" "$MOCK_LLM_PID" 2>/dev/null || true
}
trap cleanup EXIT

echo "-- waiting for the api process to become healthy --"
for _ in $(seq 1 30); do
  if curl -sf "${BASE_URL_FOR_API}/health" > /dev/null 2>&1; then
    break
  fi
  sleep 1
done
if ! curl -sf "${BASE_URL_FOR_API}/health" > /dev/null 2>&1; then
  echo "api process never became healthy -- see ${RESULTS_DIR}/api.log" >&2
  exit 1
fi

run_layer() {
  local name="$1" script="$2"
  echo "-- layer: ${name} --"
  # MSYS_NO_PATHCONV: on Git Bash / Windows, the in-container path
  # "/scripts/<script>" below otherwise gets silently mangled into a
  # Windows filesystem path (MSYS's automatic POSIX->Windows path
  # conversion, applied to every argument that *looks* like an absolute
  # path before docker.exe ever sees it) -- verified directly: without
  # this, docker run received "C:/Program Files/Git/scripts/<script>"
  # instead of the in-container path and failed to find the module. A
  # harmless no-op on Linux/Mac.
  MSYS_NO_PATHCONV=1 docker run --rm --add-host=host.docker.internal:host-gateway \
    -e BASE_URL="$BASE_URL" -e WORKSPACE_ID="$WORKSPACE_ID" -e API_KEY="$API_KEY" \
    -e CONTEXT_SUBJECT_ID="${CONTEXT_SUBJECT_ID:-}" -e LLM_SELLER_ID="${LLM_SELLER_ID:-}" \
    -v "$(pwd)/loadtest:/scripts" \
    grafana/k6 run "/scripts/${script}" \
    | tee "${RESULTS_DIR}/${name}.log"
}

run_layer "01_ingestion_throughput" "k6_ingestion_throughput.js"
run_layer "02_context_retrieval" "k6_context_retrieval.js"
run_layer "03_llm_concurrency" "k6_llm_concurrency.js"

{
  echo "Phase 10 load-test baseline -- ${TIMESTAMP}"
  echo "workspace_id=${WORKSPACE_ID} base_url=${BASE_URL} mock_llm_port=${MOCK_LLM_PORT}"
  echo
  echo "This is this system's own measured baseline on this machine, not a"
  echo "pass/fail result against the source brief's vendor-scale SLOs"
  echo "(docs/evaluation.md's B6 explicitly rejects adopting those numbers"
  echo "here). Re-run after a change and diff the two reports' summaries."
  echo
  for f in "${RESULTS_DIR}"/*.log; do
    echo "== $(basename "$f") =="
    grep -E "http_req_duration|http_req_failed|iterations|vus_max|checks" "$f" || true
    echo
  done
} > "${RESULTS_DIR}/summary.txt"

echo "== baseline complete =="
echo "report: ${RESULTS_DIR}/summary.txt"
