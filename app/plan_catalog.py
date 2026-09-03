"""Canonical SaaS plan catalog — shared features; tiers differ by branches & price only."""

from __future__ import annotations

from app.models import SaaSPlanTier

# Same feature list for every package (branch count is the tier differentiator).
SHARED_FEATURES = [
    "POS & barcode scanning",
    "Inventory & stock alerts",
    "Customer CRM",
    "TRA EFD receipts",
    "Staff roles (RBAC)",
    "Reports & analytics",
    "AI business insights",
    "Offline POS mode",
]

SHARED_FEATURES_SW = [
    "POS na barcode",
    "Hifadhi na arifa za stoo",
    "CRM ya wateja",
    "Risiti TRA EFD",
    "Mamlaka za wafanyakazi (RBAC)",
    "Ripoti na uchambuzi",
    "Ushauri wa AI",
    "POS bila mtandao",
]

SHARED_MAX_STAFF = 15
SHARED_MAX_PRODUCTS = 99999

TIER_SPECS: list[dict] = [
    {
        "tier": SaaSPlanTier.starter,
        "name": "Plan 1 — Starter",
        "name_sw": "Mpango 1 — Starter",
        "tag_en": "1 branch — single shop",
        "tag_sw": "Tawi 1 — duka moja",
        "price_monthly_tzs": 49000,
        "price_yearly_tzs": 490000,
        "max_branches": 1,
        "contact_us": False,
        "popular": False,
        "sort_order": 1,
    },
    {
        "tier": SaaSPlanTier.biashara_pro,
        "name": "Plan 2 — Biashara Pro",
        "name_sw": "Mpango 2 — Biashara Pro",
        "tag_en": "2 branches — growing business",
        "tag_sw": "Matawi 2 — biashara inayokua",
        "price_monthly_tzs": 99000,
        "price_yearly_tzs": 990000,
        "max_branches": 2,
        "contact_us": False,
        "popular": True,
        "sort_order": 2,
    },
    {
        "tier": SaaSPlanTier.enterprise_chain,
        "name": "Plan 3 — Enterprise",
        "name_sw": "Mpango 3 — Biashara Kubwa",
        "tag_en": "3 branches — store chain",
        "tag_sw": "Matawi 3 — minyororo ya maduka",
        "price_monthly_tzs": 249000,
        "price_yearly_tzs": 2490000,
        "max_branches": 3,
        "contact_us": False,
        "popular": False,
        "sort_order": 3,
    },
]


def build_default_plans() -> list[dict]:
    out: list[dict] = []
    for spec in TIER_SPECS:
        out.append(
            {
                **spec,
                "features": list(SHARED_FEATURES),
                "features_sw": list(SHARED_FEATURES_SW),
                "max_staff": SHARED_MAX_STAFF,
                "max_products": SHARED_MAX_PRODUCTS,
            }
        )
    return out


DEFAULT_PLANS = build_default_plans()
