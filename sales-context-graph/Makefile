.PHONY: up down smoke test test-unit test-integration demo lint format loadtest backup-verify

up:
	docker compose up -d neo4j redis

down:
	docker compose down

# Fast observability gate (Phase 0, docs/evaluation.md): the api service
# actually serving traffic plus its /metrics scrape target exposing the 9
# named metrics from docs/plan.md §14. Not a substitute for
# test-integration -- this only proves the process boots and the
# endpoints answer, in seconds rather than minutes.
smoke:
	docker compose up -d neo4j redis api
	@echo "waiting for api..."
	@for i in $$(seq 1 20); do curl -sf http://localhost:8000/health > /dev/null && break; sleep 1; done
	curl -sf http://localhost:8000/health
	@echo ""
	curl -sf http://localhost:8000/metrics | grep -E "^scg_(ingestion_jobs|extraction_windows|extraction_provider_calls|candidate_generation|blocking_recall|resolution_decisions|claims|context_graph|ingestion_queue)" -o | sort -u

test-unit:
	pytest tests/unit

test-integration: up
	pytest tests/integration tests/eval tests/security

test: up
	pytest tests/

demo: up
	python demo_volkswagen.py

lint:
	ruff check src api tests

format:
	ruff format src api tests

# Phase 10 (docs/evaluation.md's B6) -- k6 across 3 layers (ingestion
# throughput, Context Graph retrieval latency, LLM-call concurrency
# against a local mock, not real API spend). Requires `API_KEY=<real key
# from WORKSPACE_API_KEYS>` and Docker (for the grafana/k6 image -- no
# local k6 install needed). See loadtest/README.md.
loadtest: up
	bash loadtest/run_baseline.sh

# Destructive local round-trip check: writes a marker, backs up the quiesced
# Neo4j volume, restores it, and verifies the marker returned. See the script
# header for the exact safety boundary; this never targets production Aura.
backup-verify: up
	bash scripts/verify_backup_restore.sh
