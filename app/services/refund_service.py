"""POS sale refunds — partial qty, partial amount, full remainder."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Customer, Product, Sale, StockMovement, User
from app.schemas import SaleRefundRequest

REFUNDABLE_STATUSES = frozenset({"completed", "pending_credit", "partially_refunded"})


def _now_str() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M")


def sale_refunded_total(sale: Sale) -> float:
    if sale.refunded_total and sale.refunded_total > 0:
        return float(sale.refunded_total)
    refunds = sale.refunds or []
    return round(sum(float(r.get("amount") or 0) for r in refunds), 2)


def sale_refundable_money(sale: Sale) -> float:
    if sale.status == "refunded":
        return 0.0
    return max(0.0, round(float(sale.total or 0) - sale_refunded_total(sale), 2))


def _item_refundable_qty(item: dict[str, Any]) -> float:
    qty = float(item.get("quantity") or 0)
    refunded = float(item.get("refunded_quantity") or item.get("refundedQuantity") or 0)
    return max(0.0, qty - refunded)


def _line_unit_total(item: dict[str, Any]) -> float:
    qty = float(item.get("quantity") or 0)
    total = float(item.get("total") or item.get("total_price") or 0)
    unit = float(item.get("unit_price") or item.get("unitPrice") or 0)
    if qty <= 0:
        return unit
    return total / qty if total else unit


def _normalize_items(items: list) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in items or []:
        item = dict(raw)
        if "product_id" not in item and "productId" in item:
            item["product_id"] = item["productId"]
        if "product_name" not in item and "productName" in item:
            item["product_name"] = item["productName"]
        if "refunded_quantity" not in item:
            item["refunded_quantity"] = float(item.get("refundedQuantity") or 0)
        out.append(item)
    return out


async def apply_sale_refund(
    db: AsyncSession,
    *,
    sale: Sale,
    user: User,
    tenant_id: str,
    body: SaleRefundRequest,
) -> Sale:
    if sale.status not in REFUNDABLE_STATUSES:
        raise HTTPException(status_code=400, detail="Sale cannot be refunded")
    remaining = sale_refundable_money(sale)
    if remaining <= 0:
        raise HTTPException(status_code=400, detail="Nothing left to refund")

    reason = (body.reason or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="Refund reason is required")

    kind = (body.kind or "full").strip().lower()
    restore_stock = body.restore_stock if body.restore_stock is not None else kind != "amount"
    prior_refunded = sale_refunded_total(sale)
    items = _normalize_items(sale.items)
    refund_amount = 0.0
    refund_lines: list[dict[str, Any]] = []

    if kind == "full":
        refund_amount = remaining
        for item in items:
            qty_back = _item_refundable_qty(item)
            if qty_back <= 0:
                continue
            line_amt = round(_line_unit_total(item) * qty_back, 2)
            refund_lines.append(
                {
                    "product_id": item.get("product_id"),
                    "product_name": item.get("product_name"),
                    "quantity": qty_back,
                    "line_amount": line_amt,
                }
            )
            item["refunded_quantity"] = float(item.get("quantity") or 0)
    elif kind == "items":
        picks = body.item_quantities or {}
        for item in items:
            pid = str(item.get("product_id") or "")
            want = int(float(picks.get(pid) or picks.get(str(pid)) or 0))
            if want <= 0:
                continue
            max_q = _item_refundable_qty(item)
            qty = min(want, int(max_q))
            if qty <= 0:
                continue
            line_amt = round(_line_unit_total(item) * qty, 2)
            refund_lines.append(
                {
                    "product_id": pid,
                    "product_name": item.get("product_name"),
                    "quantity": qty,
                    "line_amount": line_amt,
                }
            )
            refund_amount += line_amt
            item["refunded_quantity"] = float(item.get("refunded_quantity") or 0) + qty
        refund_amount = round(refund_amount, 2)
        if refund_amount <= 0:
            raise HTTPException(status_code=400, detail="Select at least one item quantity to refund")
        if refund_amount > remaining:
            raise HTTPException(
                status_code=400,
                detail=f"Refund total exceeds remaining refundable (max TSh {remaining:,.0f})",
            )
    elif kind == "amount":
        amt = round(float(body.amount or 0), 2)
        if amt <= 0:
            raise HTTPException(status_code=400, detail="Enter a refund amount")
        if amt > remaining:
            raise HTTPException(
                status_code=400,
                detail=f"Maximum refundable is TSh {remaining:,.0f}",
            )
        refund_amount = amt
    else:
        raise HTTPException(status_code=400, detail="Invalid refund type")

    operator = user.name or "Cashier"
    now_str = _now_str()

    if restore_stock and kind in ("full", "items"):
        for line in refund_lines:
            pid = line.get("product_id")
            qty = float(line.get("quantity") or 0)
            if not pid or qty <= 0:
                continue
            pr = await db.execute(
                select(Product).where(Product.id == pid, Product.tenant_id == tenant_id)
            )
            product = pr.scalar_one_or_none()
            if not product:
                continue
            prev = float(product.stock or 0)
            product.stock = prev + qty
            db.add(
                StockMovement(
                    tenant_id=tenant_id,
                    product_id=product.id,
                    product_name=product.name,
                    sku=product.sku,
                    movement_type="in_adjustment",
                    quantity=qty,
                    previous_stock=prev,
                    new_stock=product.stock,
                    reference_id=sale.receipt_number,
                    reference_type="SALE",
                    operator_name=operator,
                    notes=f"Refund ×{qty} ({sale.receipt_number})",
                )
            )

    credit_reverse = min(refund_amount, float(sale.balance_remaining or 0))
    balance_remaining = max(0.0, round(float(sale.balance_remaining or 0) - credit_reverse, 2))
    paid_amount = max(0.0, round(float(sale.paid_amount or 0) - (refund_amount - credit_reverse), 2))

    new_refunded_total = round(prior_refunded + refund_amount, 2)
    fully_refunded = new_refunded_total >= float(sale.total or 0) - 1

    record = {
        "id": f"ref-{sale.id}-{secrets.token_hex(4)}",
        "date": now_str,
        "reason": reason,
        "operator_name": operator,
        "amount": refund_amount,
        "kind": kind,
        "lines": refund_lines,
    }
    refunds = list(sale.refunds or [])
    refunds.append(record)

    sale.items = items
    sale.refunds = refunds
    sale.refunded_total = new_refunded_total
    sale.balance_remaining = balance_remaining
    sale.paid_amount = paid_amount
    sale.refund_reason = reason
    sale.refunded_by = operator
    sale.status = "refunded" if fully_refunded else "partially_refunded"
    if fully_refunded:
        sale.paid_amount = 0.0
        sale.balance_remaining = 0.0
        sale.refunded_at = datetime.now(UTC)
    elif not sale.refunded_at:
        sale.refunded_at = datetime.now(UTC)

    if sale.customer_id:
        cr = await db.execute(
            select(Customer).where(Customer.id == sale.customer_id, Customer.tenant_id == tenant_id)
        )
        customer = cr.scalar_one_or_none()
        if customer:
            customer.balance = max(0.0, float(customer.balance or 0) - credit_reverse)
            customer.loyalty_points = max(
                0, int(customer.loyalty_points or 0) - int(refund_amount // 1000)
            )
            if credit_reverse > 0 and customer.balance <= 0:
                customer.dunning_stage = "cleared"

    await db.flush()
    return sale


def sale_net_revenue(sale: Sale) -> float:
    """Revenue after refunds (partial or full)."""
    if sale.status == "refunded":
        return 0.0
    return max(0.0, round(float(sale.total or 0) - sale_refunded_total(sale), 2))
