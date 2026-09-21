"""HR workspace (recruitment, time off, appraisals) — persisted per tenant + branch."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, require_tenant, require_vendor_subscription
from app.database import get_db
from app.models import TenantSettings, User

router = APIRouter(prefix="/tenant/hr", tags=["hr"], dependencies=[Depends(require_vendor_subscription)])


class HrWorkspaceSnapshot(BaseModel):
    applicants: list[dict[str, Any]] = Field(default_factory=list)
    timeOff: list[dict[str, Any]] = Field(default_factory=list)
    appraisals: list[dict[str, Any]] = Field(default_factory=list)


def _branch_key(branch_id: str | None) -> str:
    return branch_id if branch_id and branch_id not in ("all", "") else "all"


async def _load_settings(db: AsyncSession, tenant_id: str) -> TenantSettings:
    row = await db.get(TenantSettings, tenant_id)
    if not row:
        row = TenantSettings(tenant_id=tenant_id, document_config={}, business_settings={})
        db.add(row)
        await db.flush()
    return row


@router.get("/workspace", response_model=HrWorkspaceSnapshot)
async def get_hr_workspace(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    branch_id: str | None = Query(None),
):
    tenant_id = require_tenant(user)
    settings = await _load_settings(db, tenant_id)
    hr_root = settings.business_settings.get("hr_by_branch") or {}
    if not isinstance(hr_root, dict):
        hr_root = {}
    snap = hr_root.get(_branch_key(branch_id)) or {}
    return HrWorkspaceSnapshot(
        applicants=snap.get("applicants") or [],
        timeOff=snap.get("timeOff") or [],
        appraisals=snap.get("appraisals") or [],
    )


@router.put("/workspace", response_model=HrWorkspaceSnapshot)
async def save_hr_workspace(
    body: HrWorkspaceSnapshot,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    branch_id: str | None = Query(None),
):
    tenant_id = require_tenant(user)
    settings = await _load_settings(db, tenant_id)
    bs = dict(settings.business_settings or {})
    hr_root = dict(bs.get("hr_by_branch") or {})
    key = _branch_key(branch_id)
    hr_root[key] = {
        "applicants": body.applicants,
        "timeOff": body.timeOff,
        "appraisals": body.appraisals,
    }
    bs["hr_by_branch"] = hr_root
    settings.business_settings = bs
    await db.flush()
    return body
