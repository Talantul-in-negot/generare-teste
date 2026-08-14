from __future__ import annotations

import sqlite3

import pytest

from graphrag.ingestion.relational import (
    EntityTableMapping,
    RelationTableMapping,
    RelationalGraphIngestor,
    RelationalGraphMapping,
    SQLiteSourceConnector,
)


def _db(tmp_path):
    path = tmp_path / "sustainability.db"
    with sqlite3.connect(path) as db:
        db.executescript("""
        CREATE TABLE suppliers (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT);
        CREATE TABLE materials (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT);
        CREATE TABLE supplies (supplier_id TEXT NOT NULL, material_id TEXT NOT NULL, confidence REAL);
        INSERT INTO suppliers VALUES ('s1', 'Supplier One', 'supplier');
        INSERT INTO materials VALUES ('m1', 'Material One', 'material');
        INSERT INTO supplies VALUES ('s1', 'm1', 0.9);
        """)
    return path


def _mapping():
    return RelationalGraphMapping(
        id="supply-chain",
        version="1.0.0",
        source_id="supplier-db",
        tenant="sustainability",
        entities=[
            EntityTableMapping(table="suppliers", entity_type="SUPPLIER", id_column="id", name_column="name"),
            EntityTableMapping(table="materials", entity_type="MATERIAL", id_column="id", name_column="name"),
        ],
        relations=[RelationTableMapping(
            table="supplies", source_table="suppliers", target_table="materials",
            source_column="supplier_id", target_column="material_id",
            relation="SUPPLIES", confidence_column="confidence",
        )],
    )


@pytest.mark.asyncio
async def test_local_connector_emits_deterministic_json_rows(tmp_path):
    path = _db(tmp_path)
    connector = SQLiteSourceConnector(path)
    source = type("Source", (), {"id": "supplier-db"})()
    mapping = _mapping().as_source_mapping()

    rows = [row async for row in connector.records(source, mapping)]

    assert len(rows) == 3
    assert rows[0].content_type == "application/json"
    assert rows[0].metadata["source_id"] == "supplier-db"


@pytest.mark.asyncio
async def test_validation_rejects_missing_required_values_before_writes(tmp_path):
    path = _db(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO suppliers VALUES ('s2', '', 'invalid')")
    writer = object()
    report = await RelationalGraphIngestor(SQLiteSourceConnector(path), writer).validate(_mapping())

    assert report.valid is False
    assert "suppliers: every row needs id and name" in report.errors


@pytest.mark.asyncio
async def test_invalid_mapping_does_not_call_graph_writer(tmp_path):
    path = _db(tmp_path)

    class Writer:
        async def write_document(self, _doc):
            raise AssertionError("writes must not begin after validation failure")

    mapping = _mapping()
    mapping.relations[0].source_table = "missing"
    ingestor = RelationalGraphIngestor(SQLiteSourceConnector(path), Writer())
    with pytest.raises(ValueError, match="unknown entity"):
        await ingestor.ingest(mapping)


def test_mapping_digest_is_stable_and_secret_free():
    mapping = _mapping().as_source_mapping()
    assert mapping.config_digest == mapping.canonical_digest()
    with pytest.raises(ValueError, match="secret"):
        mapping.__class__(
            tenant="sustainability", source_id="supplier-db", version="1.0.0",
            mapping={"password": "should-not-be-here"},
        )
