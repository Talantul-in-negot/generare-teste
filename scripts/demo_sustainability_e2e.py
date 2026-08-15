"""Run the synthetic sustainability PostgreSQL-to-MCP evidence workflow.

Examples:
  python scripts/demo_sustainability_e2e.py --database-url postgresql+asyncpg://user:pass@localhost:5432/demo --seed
  python scripts/demo_sustainability_e2e.py --database-url postgresql+asyncpg://user:pass@localhost:5432/demo

The optional seed creates only ``demo_sustainability_*`` tables with synthetic
records. It never accesses real supplier, carbon, facility or ESG data.
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from graphrag.graph.controlled_query import execute_controlled_query
from graphrag.ingestion.graph_writer import GraphWriter
from graphrag.ingestion.relational import (
    EntityTableMapping,
    PostgreSQLSourceConnector,
    RelationTableMapping,
    RelationalGraphIngestor,
    RelationalGraphMapping,
)


def mapping(tenant: str) -> RelationalGraphMapping:
    return RelationalGraphMapping(
        id="synthetic-sustainability-postgres",
        version="1.0.0",
        source_id="synthetic-sustainability-postgres",
        tenant=tenant,
        entities=[
            EntityTableMapping(table="demo_sustainability_suppliers", entity_type="SUPPLIER", id_column="id", name_column="name", description_column="description"),
            EntityTableMapping(table="demo_sustainability_materials", entity_type="MATERIAL", id_column="id", name_column="name", description_column="description"),
            EntityTableMapping(table="demo_sustainability_emissions", entity_type="EMISSIONS_RECORD", id_column="id", name_column="name", description_column="description"),
            EntityTableMapping(table="demo_sustainability_evidence", entity_type="EVIDENCE", id_column="id", name_column="name", description_column="description"),
        ],
        relations=[
            RelationTableMapping(table="demo_sustainability_supplies", source_table="demo_sustainability_suppliers", target_table="demo_sustainability_materials", source_column="supplier_id", target_column="material_id", relation="SUPPLIES", confidence_column="confidence"),
            RelationTableMapping(table="demo_sustainability_reported", source_table="demo_sustainability_suppliers", target_table="demo_sustainability_emissions", source_column="supplier_id", target_column="emissions_id", relation="REPORTED", confidence_column="confidence"),
            RelationTableMapping(table="demo_sustainability_has_evidence", source_table="demo_sustainability_emissions", target_table="demo_sustainability_evidence", source_column="emissions_id", target_column="evidence_id", relation="HAS_EVIDENCE", confidence_column="confidence"),
        ],
        ontology_version="synthetic-sustainability-supply-chain@1.0.0",
    )


async def seed(database_url: str) -> None:
    engine = create_async_engine(database_url)
    tables = [
        "demo_sustainability_suppliers", "demo_sustainability_materials",
        "demo_sustainability_emissions", "demo_sustainability_evidence",
        "demo_sustainability_supplies", "demo_sustainability_reported",
        "demo_sustainability_has_evidence",
    ]
    async with engine.begin() as connection:
        for statement in [
            "CREATE TABLE IF NOT EXISTS demo_sustainability_suppliers (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT)",
            "CREATE TABLE IF NOT EXISTS demo_sustainability_materials (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT)",
            "CREATE TABLE IF NOT EXISTS demo_sustainability_emissions (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT)",
            "CREATE TABLE IF NOT EXISTS demo_sustainability_evidence (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT)",
            "CREATE TABLE IF NOT EXISTS demo_sustainability_supplies (supplier_id TEXT, material_id TEXT, confidence REAL)",
            "CREATE TABLE IF NOT EXISTS demo_sustainability_reported (supplier_id TEXT, emissions_id TEXT, confidence REAL)",
            "CREATE TABLE IF NOT EXISTS demo_sustainability_has_evidence (emissions_id TEXT, evidence_id TEXT, confidence REAL)",
        ]:
            await connection.execute(text(statement))
        for table in tables:
            await connection.execute(text(f"DELETE FROM {table}"))
        await connection.execute(text("INSERT INTO demo_sustainability_suppliers VALUES "
                                      "('s1', 'Northwind Components', 'Synthetic supplier with verified evidence'), "
                                      "('s2', 'Apex Alloy', 'Synthetic supplier needing evidence follow-up')"))
        await connection.execute(text("INSERT INTO demo_sustainability_materials VALUES "
                                      "('m1', 'Recycled Aluminium', 'Synthetic material')"))
        await connection.execute(text("INSERT INTO demo_sustainability_emissions VALUES "
                                      "('e1', 'Northwind Scope 3 record', 'Synthetic emissions record'), "
                                      "('e2', 'Apex Scope 3 record', 'Synthetic emissions record')"))
        await connection.execute(text("INSERT INTO demo_sustainability_evidence VALUES "
                                      "('v1', 'Verified emissions attestation', 'Synthetic evidence artifact')"))
        await connection.execute(text("INSERT INTO demo_sustainability_supplies VALUES "
                                      "('s1', 'm1', 0.98), ('s2', 'm1', 0.96)"))
        await connection.execute(text("INSERT INTO demo_sustainability_reported VALUES "
                                      "('s1', 'e1', 0.97), ('s2', 'e2', 0.95)"))
        await connection.execute(text("INSERT INTO demo_sustainability_has_evidence VALUES ('e1', 'v1', 0.99)"))
    await engine.dispose()


async def main(database_url: str, tenant: str, seed_data: bool) -> None:
    if seed_data:
        await seed(database_url)
    graph_writer = GraphWriter(changed_by="sustainability-e2e-demo")
    report = await RelationalGraphIngestor(
        PostgreSQLSourceConnector(database_url), graph_writer,
    ).ingest(mapping(tenant))
    result = await execute_controlled_query(
        graph_writer.neo4j_client,
        "Which suppliers lack verified emissions evidence?",
        tenant=tenant,
    )
    print(f"Imported {report.entity_rows} entity rows and {report.relation_rows} relation rows; SHACL conforms={report.shacl_conforms}.")
    if result["rows"]:
        names = ", ".join(row["supplier"] for row in result["rows"])
        print(f"MCP grounded result: {names} lack verified emissions evidence.")
    else:
        print("MCP grounded result: all suppliers have verified emissions evidence.")
    print("Evidence is synthetic; source, mapping, ontology and relation provenance are retained in Neo4j.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Synthetic sustainability PostgreSQL-to-MCP demo")
    parser.add_argument("--database-url", required=True, help="Local PostgreSQL SQLAlchemy async URL")
    parser.add_argument("--tenant", default="sustainability")
    parser.add_argument("--seed", action="store_true", help="Seed only named synthetic demo tables")
    args = parser.parse_args()
    asyncio.run(main(args.database_url, args.tenant, args.seed))
