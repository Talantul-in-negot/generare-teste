"""Deployment invariants for horizontally scaled ingestion consumers."""

from pathlib import Path

import yaml


ROOT = Path(__file__).parents[2]


def test_dev_ingestion_worker_is_replica_safe():
    compose = yaml.safe_load((ROOT / "compose.dev.yaml").read_text(encoding="utf-8"))
    worker = compose["services"]["ingestion_worker"]
    assert "container_name" not in worker
    assert "ports" not in worker
    assert worker["healthcheck"]["test"][-1].endswith("/ready")


def test_production_ingestion_worker_is_replica_safe():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    assert "container_name" not in compose["services"]["ingestion_worker"]

