"""Low-stock and expiry alert payloads for pharmacy / inventory tenants."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.branch_scope import branch_id_filter, get_staff_branch_id
from app.models import Product, User
from app.services.branch_service import get_tenant_default_branch_id


def _expiry_days_left(expiry: datetime | None, now: datetime) -> int | None:
    if not expiry:
        return None
    exp = expiry.replace(tzinfo=UTC) if expiry.tzinfo is None else expiry
    return (exp.date() - now.date()).days


async def build_inventory_alerts(
    db: AsyncSession,
    user: User,
    *,
    branch_id: str | None = None,
    low_stock_limit: int = 12,
    expiring_limit: int = 12,
    expiring_within_days: int = 60,
) -> dict[str, Any]:
    tenant_id = user.tenant_id
    if not tenant_id:
        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "low_stock": [],
            "expiring_soon": [],
            "expired": [],
            "counts": {"low_stock": 0, "expiring_soon": 0, "expired": 0, "critical_stock": 0},
        }

    staff_branch = branch_id or get_staff_branch_id(user)
    hq_branch_id = await get_tenant_default_branch_id(db, tenant_id) if staff_branch else None
    branch_clause = branch_id_filter(Product.branch_id, staff_branch, hq_branch_id)

    q = select(Product).where(Product.tenant_id == tenant_id, Product.is_active == True)  # noqa: E712
    if branch_clause is not None:
        q = q.where(branch_clause)
    result = await db.execute(q)
    products = list(result.scalars().all())

    now = datetime.now(UTC)
    horizon = now + timedelta(days=expiring_within_days)

    low_stock: list[dict[str, Any]] = []
    expiring_soon: list[dict[str, Any]] = []
    expired: list[dict[str, Any]] = []
    critical_stock = 0

    for p in products:
        meta = p.metadata_json or {}
        image_url = meta.get("image_url") or meta.get("imageUrl")
        if p.stock <= p.reorder_point:
            severity = "critical" if p.stock <= max(1, p.reorder_point * 0.25) else "low"
            if severity == "critical":
                critical_stock += 1
            low_stock.append(
                {
                    "product_id": p.id,
                    "name": p.name,
                    "sku": p.sku,
                    "stock": p.stock,
                    "reorder_point": p.reorder_point,
                    "unit": p.unit,
                    "requires_prescription": p.requires_prescription,
                    "severity": severity,
                    "image_url": image_url,
                }
            )
        if p.expiry_date:
            exp = p.expiry_date.replace(tzinfo=UTC) if p.expiry_date.tzinfo is None else p.expiry_date
            days_left = _expiry_days_left(exp, now)
            row = {
                "product_id": p.id,
                "name": p.name,
                "sku": p.sku,
                "batch_number": p.batch_number,
                "expiry_date": exp.date().isoformat(),
                "days_left": days_left,
                "stock": p.stock,
                "requires_prescription": p.requires_prescription,
                "image_url": image_url,
            }
            if exp <= now:
                expired.append(row)
            elif exp <= horizon:
                expiring_soon.append(row)

    low_stock.sort(key=lambda x: (x["severity"] != "critical", x["stock"]))
    expiring_soon.sort(key=lambda x: x["days_left"] if x["days_left"] is not None else 9999)
    expired.sort(key=lambda x: x["expiry_date"])

    return {
        "generated_at": now.isoformat(),
        "low_stock": low_stock[:low_stock_limit],
        "expiring_soon": expiring_soon[:expiring_limit],
        "expired": expired[:expiring_limit],
        "counts": {
            "low_stock": len(low_stock),
            "expiring_soon": len(expiring_soon),
            "expired": len(expired),
            "critical_stock": critical_stock,
        },
    }
