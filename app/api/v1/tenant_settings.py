"""Tenant document templates and business settings."""

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, require_tenant
from app.database import get_db
from app.models import Tenant, TenantSettings, User

router = APIRouter(prefix="/tenant", tags=["tenant-settings"])


class TenantSettingsOut(BaseModel):
    document_config: dict[str, Any]
    business_settings: dict[str, Any]
    updated_at: str | None = None


class TenantSettingsUpdate(BaseModel):
    document_config: dict[str, Any] | None = None
    business_settings: dict[str, Any] | None = None


DEFAULT_DOCUMENT_CONFIG: dict[str, Any] = {
    "activeTemplateIds": {
        "invoice": "inv-classic-teal",
        "delivery_note": "dn-classic-teal",
        "order_note": "on-classic-teal",
    },
    "branding": {
        "logoUrl": "",
        "companyName": "",
        "footerText": "",
        "watermark": "",
        "address": "",
        "phone": "",
        "tinNumber": "",
    },
    "customTemplates": [],
    "updatedAt": "",
}

DEFAULT_BUSINESS_SETTINGS: dict[str, Any] = {
    "showDiscountOnReceipts": True,
    "showDiscountOnDocuments": True,
}


async def _get_or_create_settings(db: AsyncSession, tenant_id: str, tenant: Tenant) -> TenantSettings:
    result = await db.execute(select(TenantSettings).where(TenantSettings.tenant_id == tenant_id))
    row = result.scalar_one_or_none()
    if row:
        return row
    doc = {
        **DEFAULT_DOCUMENT_CONFIG,
        "branding": {
            **DEFAULT_DOCUMENT_CONFIG["branding"],
            "companyName": tenant.name,
            "tinNumber": tenant.tin_number or "",
            "phone": tenant.owner_phone or "",
        },
        "updatedAt": datetime.now(UTC).isoformat(),
    }
    row = TenantSettings(
        tenant_id=tenant_id,
        document_config=doc,
        business_settings=dict(DEFAULT_BUSINESS_SETTINGS),
    )
    db.add(row)
    await db.flush()
    return row


@router.get("/settings", response_model=TenantSettingsOut)
async def get_tenant_settings(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    tenant_result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = tenant_result.scalar_one_or_none()
    if not tenant:
        return TenantSettingsOut(
            document_config=DEFAULT_DOCUMENT_CONFIG,
            business_settings=DEFAULT_BUSINESS_SETTINGS,
        )
    row = await _get_or_create_settings(db, tenant_id, tenant)
    return TenantSettingsOut(
        document_config=row.document_config or DEFAULT_DOCUMENT_CONFIG,
        business_settings=row.business_settings or DEFAULT_BUSINESS_SETTINGS,
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
    )


@router.put("/settings", response_model=TenantSettingsOut)
async def update_tenant_settings(
    body: TenantSettingsUpdate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    tenant_result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = tenant_result.scalar_one_or_none()
    if not tenant:
        return TenantSettingsOut(
            document_config=body.document_config or DEFAULT_DOCUMENT_CONFIG,
            business_settings=body.business_settings or DEFAULT_BUSINESS_SETTINGS,
        )
    row = await _get_or_create_settings(db, tenant_id, tenant)
    if body.document_config is not None:
        merged = {**(row.document_config or {}), **body.document_config}
        merged["updatedAt"] = datetime.now(UTC).isoformat()
        row.document_config = merged
    if body.business_settings is not None:
        row.business_settings = {**(row.business_settings or {}), **body.business_settings}
    await db.flush()
    return TenantSettingsOut(
        document_config=row.document_config,
        business_settings=row.business_settings,
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
    )
