"""Geo territory analytics from real customer addresses and branch-scoped sales."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Customer, Product, Sale
from app.services.branch_service import get_tenant_default_branch_id

# Tanzania location hints → lat, lng, region (street-level approximations)
_GEO_HINTS: list[tuple[str, float, float, str]] = [
    ("kariakoo", -6.8167, 39.2833, "Dar es Salaam"),
    ("kinondoni", -6.7847, 39.2543, "Dar es Salaam"),
    ("sinza", -6.7500, 39.2167, "Dar es Salaam"),
    ("mikocheni", -6.7450, 39.2500, "Dar es Salaam"),
    ("mwenge", -6.7489, 39.2336, "Dar es Salaam"),
    ("tegeta", -6.7044, 39.2456, "Dar es Salaam"),
    ("temeke", -6.8563, 39.2542, "Dar es Salaam"),
    ("ilala", -6.8235, 39.2695, "Dar es Salaam"),
    ("ubungo", -6.7760, 39.2030, "Dar es Salaam"),
    ("dar es salaam", -6.7924, 39.2083, "Dar es Salaam"),
    ("arusha", -3.3869, 36.6830, "Arusha"),
    ("mwanza", -2.5164, 32.9175, "Mwanza"),
    ("dodoma", -6.1630, 35.7516, "Dodoma"),
    ("mbeya", -8.9094, 33.4608, "Mbeya"),
    ("morogoro", -6.8278, 37.6591, "Morogoro"),
    ("tanga", -5.0689, 39.0988, "Tanga"),
    ("zanzibar", -6.1659, 39.2026, "Zanzibar"),
]


def _location_label(address: str) -> str:
    if not address:
        return "Unknown"
    return address.split(",")[0].strip() or address.strip()


def _geocode(address: str) -> tuple[float, float, str]:
    lower = (address or "").lower()
    for hint, lat, lng, region in _GEO_HINTS:
        if hint in lower:
            return lat, lng, region
    if "," in address:
        tail = address.split(",")[-1].strip().lower()
        for hint, lat, lng, region in _GEO_HINTS:
            if hint in tail:
                return lat, lng, region
    return -6.7924, 39.2083, "Dar es Salaam"


async def build_geo_territory(
    db: AsyncSession,
    tenant_id: str,
    branch_id: str | None = None,
) -> dict[str, Any]:
    cq = select(Customer).where(Customer.tenant_id == tenant_id)
    if branch_id:
        cq = cq.where(Customer.branch_id == branch_id)
    customers = (await db.execute(cq)).scalars().all()
    customer_by_id = {c.id: c for c in customers}

    pq = select(Product).where(Product.tenant_id == tenant_id, Product.is_active == True)  # noqa: E712
    if branch_id:
        pq = pq.where(Product.branch_id == branch_id)
    products = (await db.execute(pq)).scalars().all()
    product_by_id = {p.id: p for p in products}

    sq = select(Sale).where(Sale.tenant_id == tenant_id).order_by(Sale.created_at.desc()).limit(5000)
    if branch_id:
        hq_id = await get_tenant_default_branch_id(db, tenant_id)
        if hq_id and branch_id == hq_id:
            sq = sq.where(or_(Sale.branch_id == branch_id, Sale.branch_id.is_(None)))
        else:
            sq = sq.where(Sale.branch_id == branch_id)
    sales = (await db.execute(sq)).scalars().all()

    loc_buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "total_revenue": 0.0,
            "total_units": 0.0,
            "customers": set(),
            "product_sales": defaultdict(lambda: {"name": "", "units": 0.0, "rev": 0.0}),
            "lat": 0.0,
            "lng": 0.0,
            "region": "",
        }
    )

    product_totals: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"units": 0.0, "rev": 0.0, "name": ""}
    )
    gap_count = 0

    for sale in sales:
        cust = customer_by_id.get(sale.customer_id or "")
        address = cust.address if cust else (sale.customer_name or "Walk-in")
        loc = _location_label(address)
        lat, lng, region = _geocode(address)
        bucket = loc_buckets[loc]
        bucket["lat"] = lat
        bucket["lng"] = lng
        bucket["region"] = region
        if cust:
            bucket["customers"].add(cust.id)
        bucket["total_revenue"] += float(sale.total or 0)

        for item in sale.items or []:
            pid = item.get("product_id") or item.get("productId") or ""
            qty = float(item.get("quantity") or 0)
            total = float(item.get("total") or 0)
            pname = item.get("product_name") or (product_by_id[pid].name if pid in product_by_id else "Item")
            bucket["total_units"] += qty
            ps = bucket["product_sales"][pid]
            ps["name"] = pname
            ps["units"] += qty
            ps["rev"] += total
            pt = product_totals[pid]
            pt["name"] = pname
            pt["units"] += qty
            pt["rev"] += total

    if customers and products:
        sold_pairs = {
            (s.customer_id, (item.get("product_id") or item.get("productId") or ""))
            for s in sales
            for item in (s.items or [])
            if s.customer_id
        }
        for cust in customers:
            for prod in products:
                if (cust.id, prod.id) not in sold_pairs:
                    gap_count += 1

    locations: list[dict[str, Any]] = []
    for loc_name, val in loc_buckets.items():
        p_list = sorted(val["product_sales"].values(), key=lambda x: x["units"], reverse=True)
        top = p_list[0] if p_list else {"name": "N/A", "units": 0, "rev": 0}
        low = p_list[-1] if len(p_list) > 1 else top
        cust_n = len(val["customers"])
        locations.append(
            {
                "location_name": loc_name,
                "region": val["region"] or "Tanzania",
                "lat": val["lat"],
                "lng": val["lng"],
                "total_revenue": round(val["total_revenue"], 2),
                "total_units_sold": int(val["total_units"]),
                "active_customer_count": cust_n,
                "top_selling_product_name": top["name"],
                "top_selling_product_revenue": round(top["rev"], 2),
                "lowest_selling_product_name": low["name"],
                "lowest_selling_product_revenue": round(low["rev"], 2),
                "average_order_value": round(val["total_revenue"] / max(cust_n, 1), 2),
                "penetration_score": min(100, int(val["total_units"] / max(cust_n * 5, 1) * 100)),
            }
        )
    locations.sort(key=lambda x: x["total_revenue"], reverse=True)

    ranked = sorted(product_totals.items(), key=lambda x: x[1]["units"], reverse=True)
    top_pid, top_val = ranked[0] if ranked else ("", {"name": "", "units": 0, "rev": 0})
    low_pid, low_val = ranked[-1] if ranked else ("", {"name": "", "units": 0, "rev": 0})

    cross_sell_potential = gap_count * 25000

    return {
        "locations": locations,
        "summary": {
            "top_product_name": top_val["name"],
            "top_product_units": int(top_val["units"]),
            "top_product_revenue": round(top_val["rev"], 2),
            "lowest_product_name": low_val["name"],
            "lowest_product_units": int(low_val["units"]),
            "top_territory_name": locations[0]["location_name"] if locations else "",
            "top_territory_revenue": locations[0]["total_revenue"] if locations else 0,
            "top_territory_customers": locations[0]["active_customer_count"] if locations else 0,
            "whitespace_gaps": gap_count,
            "cross_sell_potential_tzs": cross_sell_potential,
        },
        "customer_count": len(customers),
        "product_count": len(products),
        "sale_count": len(sales),
    }
