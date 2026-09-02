"""Tenant document templates and business settings."""



import base64

import re

from datetime import UTC, datetime

from typing import Annotated, Any



from fastapi import APIRouter, Depends, HTTPException

from pydantic import BaseModel, Field

from sqlalchemy import select

from sqlalchemy.ext.asyncio import AsyncSession



from app.core.deps import get_current_user, require_tenant

from app.core.document_catalog import DEFAULT_ACTIVE_TEMPLATES

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





class LogoUploadRequest(BaseModel):

    image_base64: str = Field(..., min_length=32, max_length=1_400_000)





DEFAULT_DOCUMENT_CONFIG: dict[str, Any] = {

    "activeTemplateIds": dict(DEFAULT_ACTIVE_TEMPLATES),

    "branding": {

        "logoUrl": "",

        "companyName": "",

        "footerText": "Thank you for your business — Asante kwa biashara yako",

        "watermark": "",

        "address": "",

        "phone": "",

        "tinNumber": "",

    },

    "customTemplates": [],

    "updatedAt": "",

}



DEFAULT_BUSINESS_SETTINGS: dict[str, Any] = {
    "discountEnabled": True,
    "maxDiscountPercent": 15,
    "showDiscountOnReceipts": True,
    "showDiscountOnDocuments": True,
    "cartDiscountEnabled": False,
    "priceOverrideEnabled": False,
    "partialPaymentEnabled": True,
    "negotiationEnabled": True,
    "vatEnabled": True,
    "vatRate": 0.18,
}



_LOGO_DATA_URL_RE = re.compile(r"^data:image/(png|jpeg|jpg|webp|gif);base64,", re.IGNORECASE)





def _deep_merge_document_config(

    existing: dict[str, Any] | None,

    incoming: dict[str, Any],

) -> dict[str, Any]:

    base = dict(existing or DEFAULT_DOCUMENT_CONFIG)

    merged = {**base, **incoming}

    if "branding" in incoming:

        merged["branding"] = {

            **(base.get("branding") or DEFAULT_DOCUMENT_CONFIG["branding"]),

            **incoming["branding"],

        }

    if "activeTemplateIds" in incoming:

        merged["activeTemplateIds"] = {

            **(base.get("activeTemplateIds") or DEFAULT_ACTIVE_TEMPLATES),

            **incoming["activeTemplateIds"],

        }

    if "customTemplates" in incoming:

        merged["customTemplates"] = incoming["customTemplates"]

    merged["updatedAt"] = datetime.now(UTC).isoformat()

    return merged





def _validate_logo_data_url(data_url: str) -> str:

    if not _LOGO_DATA_URL_RE.match(data_url):

        raise HTTPException(

            status_code=400,

            detail="Logo must be a PNG, JPEG, WEBP, or GIF data URL (data:image/...;base64,...).",

        )

    try:

        payload = data_url.split(",", 1)[1]

        raw = base64.b64decode(payload, validate=True)

    except Exception as exc:

        raise HTTPException(status_code=400, detail="Invalid base64 logo data.") from exc

    if len(raw) > 512_000:

        raise HTTPException(status_code=400, detail="Logo file is too large (max 500 KB).")

    return data_url





async def _get_or_create_settings(db: AsyncSession, tenant_id: str, tenant: Tenant) -> TenantSettings:

    result = await db.execute(select(TenantSettings).where(TenantSettings.tenant_id == tenant_id))

    row = result.scalar_one_or_none()

    if row:

        return row

    address_parts = [p for p in (tenant.district, tenant.region) if p]

    doc = {

        **DEFAULT_DOCUMENT_CONFIG,

        "branding": {

            **DEFAULT_DOCUMENT_CONFIG["branding"],

            "companyName": tenant.name,

            "tinNumber": tenant.tin_number or "",

            "phone": tenant.owner_phone or "",

            "address": ", ".join(address_parts),

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

        row.document_config = _deep_merge_document_config(row.document_config, body.document_config)

    if body.business_settings is not None:

        row.business_settings = {**(row.business_settings or {}), **body.business_settings}

    await db.flush()

    return TenantSettingsOut(

        document_config=row.document_config,

        business_settings=row.business_settings,

        updated_at=row.updated_at.isoformat() if row.updated_at else None,

    )





@router.post("/settings/logo", response_model=TenantSettingsOut)

async def upload_document_logo(

    body: LogoUploadRequest,

    user: Annotated[User, Depends(get_current_user)],

    db: Annotated[AsyncSession, Depends(get_db)],

):

    """Upload a business logo for invoices, delivery notes, and order documents."""

    tenant_id = require_tenant(user)

    tenant_result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))

    tenant = tenant_result.scalar_one_or_none()

    if not tenant:

        raise HTTPException(status_code=404, detail="Tenant not found")



    logo_url = _validate_logo_data_url(body.image_base64.strip())

    row = await _get_or_create_settings(db, tenant_id, tenant)

    row.document_config = _deep_merge_document_config(

        row.document_config,

        {"branding": {"logoUrl": logo_url}},

    )

    await db.flush()

    return TenantSettingsOut(

        document_config=row.document_config,

        business_settings=row.business_settings or DEFAULT_BUSINESS_SETTINGS,

        updated_at=row.updated_at.isoformat() if row.updated_at else None,

    )





@router.delete("/settings/logo", response_model=TenantSettingsOut)

async def remove_document_logo(

    user: Annotated[User, Depends(get_current_user)],

    db: Annotated[AsyncSession, Depends(get_db)],

):

    tenant_id = require_tenant(user)

    tenant_result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))

    tenant = tenant_result.scalar_one_or_none()

    if not tenant:

        raise HTTPException(status_code=404, detail="Tenant not found")



    row = await _get_or_create_settings(db, tenant_id, tenant)

    row.document_config = _deep_merge_document_config(

        row.document_config,

        {"branding": {"logoUrl": ""}},

    )

    await db.flush()

    return TenantSettingsOut(

        document_config=row.document_config,

        business_settings=row.business_settings or DEFAULT_BUSINESS_SETTINGS,

        updated_at=row.updated_at.isoformat() if row.updated_at else None,

    )


