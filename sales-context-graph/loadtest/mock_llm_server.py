#!/usr/bin/env python
"""Mock LLM server for loadtest/'s LLM-call-concurrency layer (Phase 10,
docs/evaluation.md's B6). Mimics just enough of Anthropic's Messages API
(POST /v1/messages) for `anthropic.AsyncAnthropic(base_url=...)` to talk to
it successfully -- see src/llm/chat.py::build_chat_fn(base_url=...) and
src/core/config.py's `llm_base_url` setting.

Why a mock instead of the real API: docs/evaluation.md's B6 explicitly asks
for LLM-call concurrency to run "against a stubbed/rate-limited target, not
real API spend" -- running k6 concurrency levels against a real, billed LLM
API would cost real money per request and conflate network/vendor variance
with this system's own behavior under load. This server injects a
configurable artificial latency (MOCK_LLM_LATENCY_MS, default 400ms) to
stand in for a real model's response time, and an optional rate-limit-error
rate (MOCK_LLM_RATE_LIMIT_PCT, default 0) so a run can also observe how this
system behaves against a degraded LLM backend without needing an actual
vendor outage.

Always returns a syntactically valid IntentClassification (src/nlq/
models.py) as the message content: intent_id="top-objections" is a real,
registered intent (src/nlq/catalog.py) needing only a seller_id, which
loadtest/k6_llm_concurrency.js supplies via AskRequest.seller_id (resolved
from caller context, not the LLM) -- so a successful mock response exercises
the full classify -> resolve -> dispatch path, not just the LLM round trip
in isolation.

stdlib only, no new dependency -- ThreadingHTTPServer handles the modest
concurrency a local load-test run needs.
"""

from __future__ import annotations

import json
import os
import random
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LATENCY_MS = int(os.environ.get("MOCK_LLM_LATENCY_MS", "400"))
RATE_LIMIT_PCT = float(os.environ.get("MOCK_LLM_RATE_LIMIT_PCT", "0"))
PORT = int(os.environ.get("MOCK_LLM_PORT", "4010"))

_CLASSIFICATION_TEXT = json.dumps({
    "intent_id": "top-objections",
    "confidence": 0.95,
    "reasoning": "mock LLM load-test response -- not a real model call",
})


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format_str: str, *args) -> None:
        pass  # k6/the orchestrating script already report request-level stats

    def do_POST(self) -> None:
        # Drain the request body regardless of path -- leaving it unread
        # under concurrent keep-alive connections risks connection resets
        # that would show up as noise in the load test's own error rate.
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length:
            self.rfile.read(content_length)

        if self.path != "/v1/messages":
            self.send_response(404)
            self.end_headers()
            return

        time.sleep(LATENCY_MS / 1000)

        if RATE_LIMIT_PCT > 0 and random.random() * 100 < RATE_LIMIT_PCT:
            self._respond(429, {
                "type": "error",
                "error": {"type": "rate_limit_error", "message": "mock_llm_server: simulated rate limit"},
            })
            return

        self._respond(200, {
            "id": "msg_mock_loadtest",
            "type": "message",
            "role": "assistant",
            "model": "mock-loadtest-model",
            "content": [{"type": "text", "text": _CLASSIFICATION_TEXT}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 50, "output_tokens": 20},
        })

    def _respond(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(
        f"mock_llm_server listening on :{PORT} "
        f"(latency={LATENCY_MS}ms, rate_limit_pct={RATE_LIMIT_PCT})",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
