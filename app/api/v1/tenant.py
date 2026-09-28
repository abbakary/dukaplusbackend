from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.business_engine import BUSINESS_PROFILES, get_business_profile
from app.core.deps import get_current_user, require_tenant
from app.database import get_db
from app.models import Branch, CalendarEvent, Product, Tenant, User

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
        "subscription_expiry": tenant.subscription_expiry.isoformat()[:10] if tenant.subscription_expiry else None,
        "tin_number": tenant.tin_number,
        "license_number": tenant.license_number,
        "tra_efd_serial": tenant.tra_efd_serial,
        "branches_count": len(tenant.branches),
        "workplace": profile,
    }


@router.post("/cleanup-demo-catalog")
async def cleanup_demo_catalog(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Remove onboarding sample SKUs (DEMO-*, starter_pack) — safe for production tenants."""
    tenant_id = require_tenant(user)
    prod_rows = await db.execute(select(Product).where(Product.tenant_id == tenant_id))
    removed_products = 0
    for product in prod_rows.scalars().all():
        sku = (product.sku or "").upper()
        meta = product.metadata_json if isinstance(product.metadata_json, dict) else {}
        if sku.startswith("DEMO-") or meta.get("starter_pack") or meta.get("showcase"):
            await db.delete(product)
            removed_products += 1

    ev_rows = await db.execute(select(CalendarEvent).where(CalendarEvent.tenant_id == tenant_id))
    removed_events = 0
    for ev in ev_rows.scalars().all():
        meta = ev.metadata_json if isinstance(ev.metadata_json, dict) else {}
        if meta.get("starter_pack"):
            await db.delete(ev)
            removed_events += 1

    await db.flush()
    return {"removed_products": removed_products, "removed_calendar_events": removed_events}


@router.get("/business-types")
async def list_business_types():
    return {
        "types": [
            {"id": k, "label_sw": v["label_sw"], "label_en": v["label_en"], "icon": v["icon"]}
            for k, v in BUSINESS_PROFILES.items()
        ]
    }
