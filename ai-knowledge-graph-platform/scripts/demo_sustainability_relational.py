"""Run the local synthetic sustainability relational-to-KG demonstration.

Usage: python scripts/demo_sustainability_relational.py [path-to-sqlite]
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import tempfile
from pathlib import Path

from graphrag.ingestion.graph_writer import GraphWriter
from graphrag.ingestion.relational import (
    EntityTableMapping,
    RelationTableMapping,
    RelationalGraphIngestor,
    RelationalGraphMapping,
    SQLiteSourceConnector,
)


def create_fixture(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.executescript("""
        CREATE TABLE suppliers (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT);
        CREATE TABLE materials (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT);
        CREATE TABLE facilities (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT);
        CREATE TABLE supplies (supplier_id TEXT NOT NULL, material_id TEXT NOT NULL, confidence REAL);
        CREATE TABLE operates (supplier_id TEXT NOT NULL, facility_id TEXT NOT NULL, confidence REAL);
        INSERT INTO suppliers VALUES ('sup-001', 'Northwind Components', 'Synthetic supplier');
        INSERT INTO materials VALUES ('mat-001', 'Recycled aluminium', 'Synthetic material');
        INSERT INTO facilities VALUES ('fac-001', 'Barcelona Assembly Site', 'Synthetic facility');
        INSERT INTO supplies VALUES ('sup-001', 'mat-001', 0.98);
        INSERT INTO operates VALUES ('sup-001', 'fac-001', 0.96);
        """)


async def main(path: Path) -> None:
    if not path.exists():
        create_fixture(path)
    mapping = RelationalGraphMapping(
        id="synthetic-sustainability-supply-chain",
        version="1.0.0",
        source_id="local-supplier-sql",
        tenant="sustainability",
        entities=[
            EntityTableMapping(table="suppliers", entity_type="SUPPLIER", id_column="id", name_column="name", description_column="description"),
            EntityTableMapping(table="materials", entity_type="MATERIAL", id_column="id", name_column="name", description_column="description"),
            EntityTableMapping(table="facilities", entity_type="FACILITY", id_column="id", name_column="name", description_column="description"),
        ],
        relations=[
            RelationTableMapping(table="supplies", source_table="suppliers", target_table="materials", source_column="supplier_id", target_column="material_id", relation="SUPPLIES", confidence_column="confidence"),
            RelationTableMapping(table="operates", source_table="suppliers", target_table="facilities", source_column="supplier_id", target_column="facility_id", relation="OPERATES", confidence_column="confidence"),
        ],
        ontology_version="synthetic-sustainability-supply-chain@1.0.0",
    )
    ingestor = RelationalGraphIngestor(SQLiteSourceConnector(path), GraphWriter(changed_by="local-demo"))
    report = await ingestor.ingest(mapping)
    print(f"Ingested {report.entity_rows} relational rows and {report.relation_rows} relation rows for tenant {report.tenant}.")
    print("Provenance: local SQLite source; mapping and ontology versions are stored on the source document.")


if __name__ == "__main__":
    demo_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.gettempdir()) / "synthetic-sustainability.db"
    asyncio.run(main(demo_path))
