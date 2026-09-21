"""Auto-post double-entry journals from POS sales and paid expenses."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Product, Sale
from app.models.accounting import JournalEntry, JournalLine
from app.schemas import SaleItemCreate
from app.services.accounting_defaults import ensure_default_chart

COMPLETED_SALE_STATUSES = frozenset({"completed", "pending_credit", "ready_to_complete"})


async def _entry_exists(db: AsyncSession, tenant_id: str, source: str, source_id: str) -> bool:
    row = await db.execute(
        select(JournalEntry.id).where(
            JournalEntry.tenant_id == tenant_id,
            JournalEntry.source == source,
            JournalEntry.source_id == source_id,
        )
    )
    return row.scalar_one_or_none() is not None


async def _add_line(
    db: AsyncSession,
    entry_id: str,
    by_code: dict,
    code: str,
    label: str,
    debit: float,
    credit: float,
) -> None:
    acct = by_code.get(code)
    if not acct or (debit <= 0 and credit <= 0):
        return
    db.add(
        JournalLine(
            entry_id=entry_id,
            account_id=acct.id,
            label=label,
            debit=round(debit, 2),
            credit=round(credit, 2),
        )
    )


async def post_sale_journal(
    db: AsyncSession,
    *,
    tenant_id: str,
    sale: Sale,
    include_vat: bool = True,
) -> None:
    if sale.status not in COMPLETED_SALE_STATUSES:
        return
    if await _entry_exists(db, tenant_id, "pos_sale", sale.id):
        return

    accounts = await ensure_default_chart(db, tenant_id)
    by_code = {a.code: a for a in accounts}
    entry_date = sale.created_at.date() if sale.created_at else date.today()
    entry = JournalEntry(
        tenant_id=tenant_id,
        branch_id=sale.branch_id,
        entry_date=entry_date,
        reference=sale.receipt_number or f"SALE-{sale.id[:8]}",
        memo=f"POS sale — {sale.customer_name or 'Walk-in'}",
        source="pos_sale",
        source_id=sale.id,
    )
    db.add(entry)
    await db.flush()

    paid = float(sale.paid_amount or 0)
    ar = float(sale.balance_remaining or 0)
    subtotal = float(sale.subtotal or 0)
    vat = float(sale.vat_amount or 0) if include_vat else 0.0
    revenue = max(0.0, round(subtotal if vat <= 0 else subtotal, 2))

    await _add_line(db, entry.id, by_code, "1000", "Cash & mobile", paid, 0)
    await _add_line(db, entry.id, by_code, "1200", "Accounts receivable", ar, 0)
    await _add_line(db, entry.id, by_code, "4000", "Sales revenue", 0, revenue)
    if vat > 0:
        await _add_line(db, entry.id, by_code, "2100", "VAT payable TRA", 0, vat)

    cogs = 0.0
    for raw in sale.items or []:
        try:
            item = raw if isinstance(raw, dict) else SaleItemCreate(**raw).model_dump()
        except Exception:
            item = raw if isinstance(raw, dict) else {}
        pid = item.get("product_id")
        qty = float(item.get("quantity") or 0)
        if not pid or qty <= 0:
            continue
        prod = await db.get(Product, pid)
        if prod and prod.tenant_id == tenant_id:
            cogs += qty * float(prod.cost or 0)
    cogs = round(cogs, 2)
    if cogs > 0:
        cogs_entry = JournalEntry(
            tenant_id=tenant_id,
            branch_id=sale.branch_id,
            entry_date=entry_date,
            reference=f"{entry.reference}-COGS",
            memo="Cost of goods sold",
            source="pos_sale_cogs",
            source_id=sale.id,
        )
        db.add(cogs_entry)
        await db.flush()
        await _add_line(db, cogs_entry.id, by_code, "5000", "COGS", cogs, 0)
        await _add_line(db, cogs_entry.id, by_code, "1300", "Inventory", 0, cogs)

    await db.flush()


async def post_expense_journal(
    db: AsyncSession,
    *,
    tenant_id: str,
    expense_id: str,
    amount: float,
    title: str,
    category: str,
    branch_id: str | None = None,
    expense_date: date | None = None,
) -> None:
    if amount <= 0:
        return
    if await _entry_exists(db, tenant_id, "expense", expense_id):
        return

    accounts = await ensure_default_chart(db, tenant_id)
    by_code = {a.code: a for a in accounts}
    expense_code = "6200" if category in ("payroll", "staff_salaries") else "6000"
    entry = JournalEntry(
        tenant_id=tenant_id,
        branch_id=branch_id,
        entry_date=expense_date or date.today(),
        reference=f"EXP-{expense_id[:8]}",
        memo=title[:200],
        source="expense",
        source_id=expense_id,
    )
    db.add(entry)
    await db.flush()
    await _add_line(db, entry.id, by_code, expense_code, title[:120], amount, 0)
    await _add_line(db, entry.id, by_code, "1000", "Cash payment", 0, amount)
    await db.flush()
