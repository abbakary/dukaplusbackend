from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.business_engine import BUSINESS_PROFILES, get_business_profile
from app.core.deps import get_current_user, require_tenant
from app.database import get_db
from app.models import Branch, Tenant, User

router = APIRouter(prefix="/tenant", tags=["tenant"])


@router.get("/profile")
async def tenant_profile(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    result = await db.execute(
        select(Tenant).options(selectinload(Tenant.branches)).where(Tenant.id == tenant_id)
    )
    tenant = result.scalar_one_or_none()
    if not tenant:
        return {"error": "Tenant not found"}

    biz_type = tenant.business_type.value
    profile = get_business_profile(biz_type)

    return {
        "tenant_id": tenant.id,
        "business_name": tenant.name,
        "business_type": biz_type,
        "region": tenant.region,
        "district": tenant.district,
        "plan": tenant.plan.value,
        "status": tenant.status.value,
        "tra_efd_serial": tenant.tra_efd_serial,
        "branches_count": len(tenant.branches),
        "workplace": profile,
    }


@router.get("/business-types")
async def list_business_types():
    return {
        "types": [
            {"id": k, "label_sw": v["label_sw"], "label_en": v["label_en"], "icon": v["icon"]}
            for k, v in BUSINESS_PROFILES.items()
        ]
    }
