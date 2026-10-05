"""Fiscal year boundaries for reporting (TZ SMEs — configurable start month)."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TenantSettings

PL_ACCOUNT_TYPES = frozenset({"income", "expense"})


async def fiscal_year_start(db: AsyncSession, tenant_id: str, as_of: date) -> date:
    month = 1
    row = await db.scalar(select(TenantSettings).where(TenantSettings.tenant_id == tenant_id))
    if row and row.business_settings:
        raw = row.business_settings.get("fiscalYearStartMonth") or row.business_settings.get("fiscal_year_start_month")
        try:
            month = int(raw)
        except (TypeError, ValueError):
            month = 1
    month = max(1, min(12, month))
    if as_of.month >= month:
        return date(as_of.year, month, 1)
    return date(as_of.year - 1, month, 1)


def account_uses_fy_initial(account_type: str) -> bool:
    return account_type in PL_ACCOUNT_TYPES
