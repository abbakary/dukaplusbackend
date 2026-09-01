from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, require_tenant, require_vendor_subscription
from app.core.ttl_cache import cache_get, cache_set, tenant_cache_key
from app.config import settings
from app.database import get_db
from app.models import User
from app.schemas import AnalyticsSnapshot
from app.services.analytics_service import build_analytics_snapshot

router = APIRouter(prefix="/analytics", tags=["analytics"], dependencies=[Depends(require_vendor_subscription)])


@router.get("/snapshot", response_model=AnalyticsSnapshot)
async def analytics_snapshot(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    range: str = Query("month", pattern="^(month|quarter|year|all)$"),
):
    """Single aggregated BI payload — avoids 4+ full list fetches on mobile/web."""
    tenant_id = require_tenant(user)
    cache_key = tenant_cache_key(tenant_id, "analytics", range)

    cached = await cache_get(cache_key)
    if cached is not None:
        return AnalyticsSnapshot(**{**cached, "cached": True})

    payload = await build_analytics_snapshot(db, tenant_id, range)
    await cache_set(cache_key, payload, settings.analytics_cache_ttl_seconds)
    return AnalyticsSnapshot(**payload)
