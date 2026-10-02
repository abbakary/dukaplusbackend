"""Vendor bill posting — Odoo-style Dr Expense/Inventory + VAT Input / Cr Payable."""

from __future__ import annotations

import json
from datetime import date, datetime, UTC

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.accounting import JournalEntry, VendorBill
from app.services.accounting_defaults import ensure_default_chart
from app.services.accounting_posting import _add_line


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


async def post_vendor_bill(db: AsyncSession, bill: VendorBill) -> JournalEntry:
    if bill.state == "posted" and bill.journal_entry_id:
        existing = await db.get(JournalEntry, bill.journal_entry_id)
        if existing:
            return existing

    accounts = await ensure_default_chart(db, tenant_id=bill.tenant_id)
    by_code = {a.code: a for a in accounts}

    lines = json.loads(bill.lines_json or "[]")
    expense_total = 0.0
    for ln in lines:
        expense_total += float(ln.get("amount") or (float(ln.get("quantity") or 0) * float(ln.get("price_unit") or 0)))

    untaxed = float(bill.amount_untaxed or expense_total)
    tax = float(bill.amount_tax or 0)
    total = float(bill.amount_total or untaxed + tax)

    entry = JournalEntry(
        tenant_id=bill.tenant_id,
        branch_id=bill.branch_id,
        entry_date=bill.bill_date,
        reference=bill.name,
        memo=f"Vendor bill — {bill.vendor_name} {bill.vendor_bill_ref}".strip(),
        source="vendor_bill",
        source_id=bill.id,
    )
    db.add(entry)
    await db.flush()

    expense_code = "5100" if by_code.get("5100") else "6000"
    await _add_line(db, entry.id, by_code, expense_code, bill.vendor_name or "Expense", untaxed, 0)
    if tax > 0:
        vat_in = "1310" if by_code.get("1310") else "1300"
        await _add_line(db, entry.id, by_code, vat_in, "VAT Input", tax, 0)
    await _add_line(db, entry.id, by_code, "2000", "Accounts Payable", 0, total)

    bill.state = "posted"
    bill.payment_state = "not_paid"
    bill.amount_residual = total
    bill.journal_entry_id = entry.id
    bill.posted_at = datetime.now(UTC)
    if bill.name == "Draft" or not bill.name.startswith("BILL/"):
        bill.name = await _next_bill_name(db, bill.tenant_id, bill.bill_date)
    await db.flush()
    return entry


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

    accounts = await ensure_default_chart(db, tenant_id=bill.tenant_id)
    by_code = {a.code: a for a in accounts}
    entry = JournalEntry(
        tenant_id=bill.tenant_id,
        branch_id=bill.branch_id,
        entry_date=date.today(),
        reference=reference or f"PAY-{bill.name}",
        memo=f"Payment — {bill.vendor_name}",
        source="vendor_payment",
        source_id=bill.id,
    )
    db.add(entry)
    await db.flush()

    await _add_line(db, entry.id, by_code, "2000", "Accounts Payable", pay, 0)
    await _add_line(db, entry.id, by_code, journal_code, "Payment", 0, pay)

    bill.amount_residual = round(float(bill.amount_residual) - pay, 2)
    if bill.amount_residual <= 0.01:
        bill.amount_residual = 0
        bill.payment_state = "paid"
    else:
        bill.payment_state = "partial"
    await db.flush()
