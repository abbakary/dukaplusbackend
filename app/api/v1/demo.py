"""On-demand demo enrichment for sample.dukaplus.co.tz tenants."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from app.core.deps import get_current_user, require_tenant
from app.database import get_db
from app.models import Tenant, User
from app.seed_sample_data import SAMPLE_EMAIL_DOMAIN
from app.seed_sample_enrichment import ENRICHMENT_VERSION, enrich_sample_tenants
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/tenant/demo", tags=["demo"])


def _is_sample_user(user: User) -> bool:
    email = (user.email or "").lower()
    return email.endswith(SAMPLE_EMAIL_DOMAIN)


@router.post("/enrich-sample")
async def enrich_sample_for_current_tenant(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Backfill product/staff images and sample journals for demo accounts."""
    if not _is_sample_user(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Demo enrichment is only available for @sample.dukaplus.co.tz accounts.",
        )
    tenant_id = require_tenant(user)
    tenant = await db.get(Tenant, tenant_id)
    if not tenant or SAMPLE_EMAIL_DOMAIN not in (tenant.owner_email or ""):
        raise HTTPException(status_code=403, detail="Tenant is not a sample demo store.")

    stats = await enrich_sample_tenants()
    return {"ok": True, "version": ENRICHMENT_VERSION, "stats": stats}
