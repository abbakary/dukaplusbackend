"""Vendor bill ↔ purchase order matching (Odoo 3-way match lite)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PurchaseOrder
from app.models.accounting import AccBillPurchaseMatch, VendorBill


async def list_po_candidates(
    db: AsyncSession,
    *,
    tenant_id: str,
    bill: VendorBill,
) -> list[dict]:
    if not bill.vendor_id:
        return []
    pos = (
        await db.execute(
            select(PurchaseOrder).where(
                PurchaseOrder.tenant_id == tenant_id,
                PurchaseOrder.supplier_id == bill.vendor_id,
                PurchaseOrder.status.in_(("confirmed", "received", "partial")),
            )
        )
    ).scalars().all()
    linked = {
        str(x)
        for x in (
            await db.execute(
                select(AccBillPurchaseMatch.purchase_order_id).where(
                    AccBillPurchaseMatch.tenant_id == tenant_id,
                    AccBillPurchaseMatch.bill_id == bill.id,
                )
            )
        ).scalars().all()
    }
    out = []
    for po in pos:
        out.append(
            {
                "id": po.id,
                "po_number": po.po_number,
                "supplier_name": po.supplier_name,
                "total_amount": po.total_amount,
                "status": po.status,
                "linked": str(po.id) in linked,
            }
        )
    return out


async def link_bill_to_po(
    db: AsyncSession,
    *,
    tenant_id: str,
    bill: VendorBill,
    purchase_order_id: str,
    matched_amount: float | None = None,
) -> AccBillPurchaseMatch:
    po = await db.get(PurchaseOrder, purchase_order_id)
    if not po or po.tenant_id != tenant_id:
        raise ValueError("Purchase order not found")
    if bill.vendor_id and po.supplier_id != bill.vendor_id:
        raise ValueError("Vendor on bill does not match PO supplier")
    amt = matched_amount if matched_amount is not None else float(bill.amount_total or po.total_amount)
    existing = await db.scalar(
        select(AccBillPurchaseMatch.id).where(
            AccBillPurchaseMatch.bill_id == bill.id,
            AccBillPurchaseMatch.purchase_order_id == purchase_order_id,
        )
    )
    if existing:
        row = await db.get(AccBillPurchaseMatch, existing)
        if row:
            row.matched_amount = amt
            await db.flush()
            return row
    match = AccBillPurchaseMatch(
        tenant_id=tenant_id,
        bill_id=bill.id,
        purchase_order_id=purchase_order_id,
        matched_amount=round(amt, 2),
    )
    db.add(match)
    bill.purchase_order_id = purchase_order_id
    await db.flush()
    return match


async def list_matches_for_bill(db: AsyncSession, *, tenant_id: str, bill_id: str) -> list[dict]:
    rows = (
        await db.execute(
            select(AccBillPurchaseMatch, PurchaseOrder)
            .join(PurchaseOrder, PurchaseOrder.id == AccBillPurchaseMatch.purchase_order_id)
            .where(AccBillPurchaseMatch.tenant_id == tenant_id, AccBillPurchaseMatch.bill_id == bill_id)
        )
    ).all()
    return [
        {
            "match_id": m.id,
            "purchase_order_id": po.id,
            "po_number": po.po_number,
            "matched_amount": m.matched_amount,
            "po_total": po.total_amount,
        }
        for m, po in rows
    ]
