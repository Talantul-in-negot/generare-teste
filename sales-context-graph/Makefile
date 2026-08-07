.PHONY: up down smoke test test-unit test-integration demo lint format

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
