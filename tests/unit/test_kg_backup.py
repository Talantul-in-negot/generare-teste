"""Storage URI routing for the graph backup CLI."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).parents[2]
SPEC = importlib.util.spec_from_file_location("kg_backup", ROOT / "scripts" / "kg_backup.py")
assert SPEC and SPEC.loader
kg_backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(kg_backup)


def test_gcs_uri_is_parsed_and_routed_as_remote():
    uri = "gs://graphrag-backups/tenant-a/graph.ndjson"
    assert kg_backup._is_gcs(uri) is True
    assert kg_backup._is_remote(uri) is True
    assert kg_backup._parse_gcs(uri) == ("graphrag-backups", "tenant-a/graph.ndjson")


def test_s3_and_local_uri_routing_is_unchanged():
    assert kg_backup._is_remote("s3://bucket/graph.ndjson") is True
    assert kg_backup._is_remote("backups/graph.ndjson") is False
    assert kg_backup._parse_s3("s3://bucket/graph.ndjson") == ("bucket", "graph.ndjson")


def test_gcs_writes_use_an_in_memory_buffer_before_upload():
    buf = kg_backup._open_write("gs://bucket/graph.ndjson")
    kg_backup._write_ndjson_line(buf, {"_type": "meta", "tenant": "tenant-a"})
    assert '"tenant": "tenant-a"' in buf.getvalue()
