"""Vendor bill posting — Odoo-style Dr Expense/Inventory + VAT Input / Cr Payable."""

from __future__ import annotations

import json
from datetime import date, datetime, UTC

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.accounting import AccMove, VendorBill
from app.services.acc_posting_service import MoveLineIn, post_move, register_vendor_payment_move
from app.services.accounting_defaults import ensure_default_chart
from app.services.fiscal_position_service import resolve_fiscal_position


async def _next_bill_name(db: AsyncSession, tenant_id: str, bill_date: date) -> str:
    prefix = f"BILL/{bill_date.year}/{bill_date.month:02d}/"
    result = await db.execute(
        select(func.count(VendorBill.id)).where(
            VendorBill.tenant_id == tenant_id,
            VendorBill.name.like(f"{prefix}%"),
        )
    )
    n = int(result.scalar() or 0) + 1
    return f"{prefix}{n:04d}"


async def post_vendor_bill(db: AsyncSession, bill: VendorBill) -> AccMove:
    if bill.state == "posted" and bill.acc_move_id:
        existing = await db.get(AccMove, bill.acc_move_id)
        if existing:
            return existing

    await ensure_default_chart(db, tenant_id=bill.tenant_id)
    fp = await resolve_fiscal_position(db, bill.tenant_id)
    vat_in_code = fp.vat_input_account or "1310"
    lines = json.loads(bill.lines_json or "[]")
    expense_total = 0.0
    for ln in lines:
        expense_total += float(ln.get("amount") or (float(ln.get("quantity") or 0) * float(ln.get("price_unit") or 0)))

    untaxed = float(bill.amount_untaxed or expense_total)
    tax = float(bill.amount_tax or 0)
    total = float(bill.amount_total or untaxed + tax)

    expense_code = "5100"
    move_lines = [
        MoveLineIn(
            expense_code,
            bill.vendor_name or "Expense",
            debit=untaxed,
            partner_id=bill.vendor_id,
            partner_name=bill.vendor_name or "",
        ),
    ]
    if tax > 0:
        move_lines.append(MoveLineIn(vat_in_code, "VAT Input", debit=tax, display_type="tax"))
    move_lines.append(
        MoveLineIn(
            "2000",
            "Accounts Payable",
            credit=total,
            partner_id=bill.vendor_id,
            partner_name=bill.vendor_name or "",
        )
    )

    if bill.name == "Draft" or not bill.name.startswith("BILL/"):
        bill.name = await _next_bill_name(db, bill.tenant_id, bill.bill_date)

    move = await post_move(
        db,
        tenant_id=bill.tenant_id,
        branch_id=bill.branch_id,
        journal_code="PUR",
        move_type="in_invoice",
        move_date=bill.bill_date,
        partner_id=bill.vendor_id,
        partner_name=bill.vendor_name or "",
        ref=bill.vendor_bill_ref or bill.name,
        narration=f"Vendor bill — {bill.vendor_name}",
        lines=move_lines,
        source_type="vendor_bill",
        source_id=bill.id,
        amount_total=total,
        mirror_journal=True,
    )

    bill.state = "posted"
    bill.payment_state = "not_paid"
    bill.amount_residual = total
    bill.acc_move_id = move.id
    bill.posted_at = datetime.now(UTC)
    await db.flush()
    return move


async def register_bill_payment(
    db: AsyncSession,
    bill: VendorBill,
    amount: float,
    *,
    journal_code: str = "1000",
    reference: str = "",
) -> None:
    if bill.state != "posted":
        raise ValueError("Bill must be posted before payment")
    pay = min(float(amount), float(bill.amount_residual or 0))
    if pay <= 0:
        return

    await register_vendor_payment_move(
        db,
        tenant_id=bill.tenant_id,
        branch_id=bill.branch_id,
        bill_id=bill.id,
        partner_id=bill.vendor_id,
        partner_name=bill.vendor_name or "",
        amount=pay,
        reference=reference or f"PAY-{bill.name}",
        journal_code="CSH" if journal_code == "1000" else "BNK",
    )

    bill.amount_residual = round(float(bill.amount_residual or 0) - pay, 2)
    if bill.amount_residual <= 0.01:
        bill.payment_state = "paid"
        bill.amount_residual = 0.0
    elif pay > 0:
        bill.payment_state = "partial" if bill.payment_state != "in_payment" else "in_payment"
    await db.flush()
