"""Products a vendor has supplied (PO lines + past bills) merged with inventory catalog."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Product, PurchaseOrder
from app.models.accounting import VendorBill


async def build_vendor_catalog(
    db: AsyncSession,
    *,
    tenant_id: str,
    vendor_id: str | None,
    limit: int = 200,
) -> tuple[list[dict[str, Any]], int]:
    by_key: dict[str, dict[str, Any]] = {}

    def upsert(
        *,
        product_id: str | None,
        name: str,
        sku: str = "",
        unit: str = "pcs",
        price_unit: float = 0,
        source: str,
    ) -> None:
        key = product_id or f"name:{name.strip().lower()}"
        if not name.strip():
            return
        row = by_key.get(key)
        if not row:
            row = {
                "product_id": product_id,
                "name": name.strip(),
                "sku": sku or "",
                "unit": unit or "pcs",
                "price_unit": round(float(price_unit or 0), 2),
                "sources": [source],
            }
            by_key[key] = row
        else:
            if source not in row["sources"]:
                row["sources"].append(source)
            if price_unit and not row["price_unit"]:
                row["price_unit"] = round(float(price_unit), 2)

    if vendor_id:
        pos = (
            await db.execute(
                select(PurchaseOrder).where(
                    PurchaseOrder.tenant_id == tenant_id,
                    PurchaseOrder.supplier_id == vendor_id,
                )
            )
        ).scalars().all()
        for po in pos:
            for raw in po.items or []:
                item = raw if isinstance(raw, dict) else {}
                upsert(
                    product_id=str(item.get("product_id") or "") or None,
                    name=str(item.get("product_name") or item.get("name") or "Item"),
                    sku=str(item.get("sku") or ""),
                    unit=str(item.get("unit") or "pcs"),
                    price_unit=float(item.get("cost") or item.get("price_unit") or item.get("unit_price") or 0),
                    source="purchase_order",
                )

        bills = (
            await db.execute(
                select(VendorBill).where(
                    VendorBill.tenant_id == tenant_id,
                    VendorBill.vendor_id == vendor_id,
                )
            )
        ).scalars().all()
        for bill in bills:
            for ln in json.loads(bill.lines_json or "[]"):
                if not isinstance(ln, dict):
                    continue
                upsert(
                    product_id=str(ln.get("product_id") or "") or None,
                    name=str(ln.get("label") or ln.get("product_name") or "Item"),
                    sku=str(ln.get("sku") or ""),
                    unit=str(ln.get("unit") or "pcs"),
                    price_unit=float(ln.get("price_unit") or 0),
                    source="vendor_bill",
                )

    products = (
        await db.execute(
            select(Product).where(Product.tenant_id == tenant_id, Product.is_active.is_(True)).limit(limit)
        )
    ).scalars().all()
    for p in products:
        upsert(
            product_id=p.id,
            name=p.name,
            sku=p.sku or "",
            unit=p.unit or "pcs",
            price_unit=float(p.cost or p.price or 0),
            source="inventory",
        )

    out = sorted(by_key.values(), key=lambda x: x["name"].lower())
    trimmed = out[:limit]
    vendor_history_count = 0
    for row in trimmed:
        sources = row.get("sources") or []
        from_vendor = "purchase_order" in sources or "vendor_bill" in sources
        row["from_vendor_history"] = from_vendor
        if from_vendor:
            vendor_history_count += 1
    return trimmed, vendor_history_count
