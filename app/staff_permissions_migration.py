"""Move payroll_profile dict out of staff.permissions and fix RBAC JSON for /auth/me."""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.core.staff_permissions import (
    extract_payroll_profile,
    normalize_staff_permissions_blob,
    upsert_payroll_contract_from_profile,
)
from app.database import AsyncSessionLocal
from app.models import StaffMember

logger = logging.getLogger(__name__)


async def migrate_staff_permissions_payroll_profile() -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(StaffMember))
        staff_rows = result.scalars().all()
        changed = 0
        for staff in staff_rows:
            raw = dict(staff.permissions or {})
            payroll = extract_payroll_profile(raw)
            cleaned = normalize_staff_permissions_blob(raw)
            if payroll:
                await upsert_payroll_contract_from_profile(session, staff, payroll)
            if cleaned != (staff.permissions or {}):
                staff.permissions = cleaned
                changed += 1
        if changed:
            await session.commit()
            logger.info("Sanitized permissions JSON for %s staff member(s)", changed)
        else:
            await session.commit()
