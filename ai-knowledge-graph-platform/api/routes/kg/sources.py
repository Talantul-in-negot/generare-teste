"""Tenant-scoped Knowledge Graph source catalog endpoints."""

from fastapi import APIRouter, Depends, HTTPException

from api.auth.dependencies import get_tenant, require_scope
from graphrag.graph.neo4j_client import get_neo4j
from graphrag.graph.source_catalog import (
    SourceCatalogRepository,
    SourceMapping,
    SourceSystem,
)


router = APIRouter()


@router.post("/sources", dependencies=[Depends(require_scope("write"))])
async def upsert_source(source: SourceSystem):
    source_id = await SourceCatalogRepository(get_neo4j()).upsert_source(source)
    return {"source_id": source_id, "tenant": source.tenant}


@router.get("/sources", dependencies=[Depends(require_scope("read"))])
async def list_sources(tenant: str = Depends(get_tenant)):
    return await SourceCatalogRepository(get_neo4j()).list_sources(tenant)


@router.get("/sources/{source_id}", dependencies=[Depends(require_scope("read"))])
async def get_source(source_id: str, tenant: str = Depends(get_tenant)):
    result = await SourceCatalogRepository(get_neo4j()).get_source(source_id, tenant)
    if not result:
        raise HTTPException(status_code=404, detail="Source not found")
    return result


@router.post("/sources/{source_id}/mappings", dependencies=[Depends(require_scope("write"))])
async def add_source_mapping(source_id: str, mapping: SourceMapping):
    if mapping.source_id != source_id:
        raise HTTPException(status_code=422, detail="Path and mapping source IDs differ")
    try:
        mapping_id = await SourceCatalogRepository(get_neo4j()).add_mapping(mapping)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"mapping_id": mapping_id, "config_digest": mapping.config_digest}
