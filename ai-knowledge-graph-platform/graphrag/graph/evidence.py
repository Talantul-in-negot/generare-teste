"""Addressable evidence and source-artifact normalization."""

from __future__ import annotations

from pydantic import BaseModel, Field


class EvidenceRecord(BaseModel):
    id: str = Field(min_length=1)
    tenant: str = Field(default="default", min_length=1)
    artifact_id: str = Field(min_length=1)
    document_id: str = ""
    chunk_id: str = ""
    span_start: int | None = Field(default=None, ge=0)
    span_end: int | None = Field(default=None, ge=0)
    content_digest: str = ""
    extraction_model: str = ""
    source_type: str = "document"


class EvidenceService:
    def __init__(self, neo4j_client):
        self._neo4j = neo4j_client

    async def upsert(self, evidence: EvidenceRecord) -> str:
        await self._neo4j.run(
            """
            MERGE (a:SourceArtifact {tenant: $tenant, id: $artifact_id})
            SET a.source_type = $source_type
            MERGE (e:Evidence {tenant: $tenant, id: $id})
            SET e += $props
            MERGE (e)-[:DERIVED_FROM]->(a)
            FOREACH (doc_id IN CASE WHEN $document_id <> '' THEN [$document_id] ELSE [] END |
              MERGE (d:Document {tenant: $tenant, id: doc_id}) MERGE (e)-[:LOCATED_IN]->(d))
            FOREACH (chunk_id IN CASE WHEN $chunk_id <> '' THEN [$chunk_id] ELSE [] END |
              MERGE (c:Chunk {tenant: $tenant, id: chunk_id}) MERGE (e)-[:LOCATED_IN]->(c))
            """,
            tenant=evidence.tenant, id=evidence.id, artifact_id=evidence.artifact_id,
            source_type=evidence.source_type,
            document_id=evidence.document_id, chunk_id=evidence.chunk_id,
            props=evidence.model_dump(mode="json"),
        )
        return evidence.id
