"""Seed platform SaaS plan catalog (idempotent)."""

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import PlatformPlan, SaaSPlanTier

DEFAULT_PLANS = [
    {
        "tier": SaaSPlanTier.free_starter,
        "name": "Mwanzo",
        "name_sw": "Mwanzo",
        "tag_en": "Single shop getting started",
        "tag_sw": "Duka moja linaloanza",
        "price_monthly_tzs": 39000,
        "price_yearly_tzs": 390000,
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
        "name": "Biashara Pro",
        "name_sw": "Biashara Pro",
        "tag_en": "Growing businesses",
        "tag_sw": "Biashara inayokua",
        "price_monthly_tzs": 79000,
        "price_yearly_tzs": 790000,
        "max_branches": 5,
        "max_staff": 15,
        "max_products": 5000,
        "features": ["TRA EFD receipts", "RBAC staff", "AI insights", "Multi-branch"],
        "features_sw": ["Risiti TRA EFD", "Mamlaka RBAC", "Ushauri wa AI", "Matawi mengi"],
        "contact_us": False,
        "popular": True,
        "sort_order": 2,
    },
    {
        "tier": SaaSPlanTier.enterprise_chain,
        "name": "Enterprise",
        "name_sw": "Biashara Kubwa",
        "tag_en": "Store chains & groups",
        "tag_sw": "Minyororo ya maduka",
        "price_monthly_tzs": 0,
        "price_yearly_tzs": 0,
        "max_branches": 99,
        "max_staff": 99,
        "max_products": 99999,
        "features": ["Unlimited scale", "API access", "Dedicated support", "Custom SLA"],
        "features_sw": ["Ukubwa usio na kikomo", "API", "Msaada maalum", "SLA maalum"],
        "contact_us": True,
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
            if existing.scalar_one_or_none():
                continue
            db.add(PlatformPlan(**spec))
        await db.commit()
