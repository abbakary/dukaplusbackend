"""Seed platform SaaS plan catalog (idempotent + updates limits/prices)."""

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import PlatformPlan, SaaSPlanTier

DEFAULT_PLANS = [
    {
        "tier": SaaSPlanTier.starter,
        "name": "Plan 1 — Starter",
        "name_sw": "Mpango 1 — Starter",
        "tag_en": "Single branch — ideal for one shop",
        "tag_sw": "Tawi moja — duka moja",
        "price_monthly_tzs": 49000,
        "price_yearly_tzs": 490000,
        "max_branches": 1,
        "max_staff": 3,
        "max_products": 500,
        "features": ["POS & barcode", "Inventory alerts", "Customer CRM", "Basic reports"],
        "features_sw": ["POS na barcode", "Arifa za stoo", "CRM ya wateja", "Ripoti za msingi"],
        "contact_us": False,
        "popular": False,
        "sort_order": 1,
    },
    {
        "tier": SaaSPlanTier.biashara_pro,
        "name": "Plan 2 — Biashara Pro",
        "name_sw": "Mpango 2 — Biashara Pro",
        "tag_en": "Growing business — up to 2 branches",
        "tag_sw": "Biashara inayokua — matawi 2",
        "price_monthly_tzs": 99000,
        "price_yearly_tzs": 990000,
        "max_branches": 2,
        "max_staff": 10,
        "max_products": 5000,
        "features": ["TRA EFD receipts", "RBAC staff", "AI insights", "2 branches"],
        "features_sw": ["Risiti TRA EFD", "Mamlaka RBAC", "Ushauri wa AI", "Matawi 2"],
        "contact_us": False,
        "popular": True,
        "sort_order": 2,
    },
    {
        "tier": SaaSPlanTier.enterprise_chain,
        "name": "Plan 3 — Enterprise",
        "name_sw": "Mpango 3 — Biashara Kubwa",
        "tag_en": "Multi-branch chains — up to 3 branches",
        "tag_sw": "Minyororo ya maduka — matawi 3",
        "price_monthly_tzs": 249000,
        "price_yearly_tzs": 2490000,
        "max_branches": 3,
        "max_staff": 15,
        "max_products": 99999,
        "features": ["3 branches", "API access", "Dedicated support", "Consolidated reports"],
        "features_sw": ["Matawi 3", "API", "Msaada maalum", "Ripoti za pamoja"],
        "contact_us": False,
        "popular": False,
        "sort_order": 3,
    },
]


async def seed_platform_plans() -> None:
    async with AsyncSessionLocal() as db:
        for spec in DEFAULT_PLANS:
            existing = await db.execute(
                select(PlatformPlan).where(PlatformPlan.tier == spec["tier"])
            )
            row = existing.scalar_one_or_none()
            if row:
                for key, val in spec.items():
                    setattr(row, key, val)
            else:
                db.add(PlatformPlan(**spec))

        await db.commit()
