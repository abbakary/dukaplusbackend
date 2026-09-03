"""Seed platform SaaS plan catalog (insert missing tiers; sync shared features only)."""

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import PlatformPlan
from app.plan_catalog import DEFAULT_PLANS, SHARED_FEATURES, SHARED_FEATURES_SW


async def seed_platform_plans() -> None:
    """Ensure all tiers exist. Do not overwrite admin-edited prices/names on redeploy."""
    async with AsyncSessionLocal() as db:
        for spec in DEFAULT_PLANS:
            existing = await db.execute(
                select(PlatformPlan).where(PlatformPlan.tier == spec["tier"])
            )
            row = existing.scalar_one_or_none()
            if row:
                row.features = list(SHARED_FEATURES)
                row.features_sw = list(SHARED_FEATURES_SW)
            else:
                db.add(PlatformPlan(**spec))

        await db.commit()
