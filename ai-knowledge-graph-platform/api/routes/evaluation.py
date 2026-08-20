"""GET /evaluation endpoints."""

from fastapi import APIRouter, Depends

from api.auth.dependencies import get_tenant
from graphrag.business_matrix.kpi_tracker import KPITracker

router = APIRouter()


@router.get("/summary")
async def evaluation_summary(window_days: int = 7, tenant: str = Depends(get_tenant)):
    tracker = KPITracker()
    return await tracker.get_summary(tenant=tenant, window_days=window_days)
