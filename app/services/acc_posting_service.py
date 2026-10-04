"""Odoo-style account.move posting (balanced, immutable when posted)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.accounting import (
    AccJournal,
    AccMove,
    AccMoveLine,
    AccPayment,
    JournalEntry,
    JournalLine,
    LedgerAccount,
)


@dataclass
class MoveLineIn:
    account_code: str
    name: str
    debit: float = 0
    credit: float = 0
    partner_id: str | None = None
    partner_name: str = ""
    display_type: str = "product"


class AccPostingError(ValueError):
    pass


async def ensure_default_journals(db: AsyncSession, tenant_id: str) -> dict[str, AccJournal]:
    defaults = [
        ("SAL", "Sales", "sale"),
        ("PUR", "Purchases", "purchase"),
        ("BNK", "Bank", "bank"),
        ("CSH", "Cash", "cash"),
        ("GEN", "Miscellaneous", "general"),
    ]
    result = await db.execute(select(AccJournal).where(AccJournal.tenant_id == tenant_id))
    out: dict[str, AccJournal] = {j.code: j for j in result.scalars().all()}
    added = False
    for code, name, jtype in defaults:
        if code not in out:
            j = AccJournal(tenant_id=tenant_id, code=code, name=name, journal_type=jtype)
            db.add(j)
            out[code] = j
            added = True
    if added:
        await db.flush()
    return out


async def _next_move_name(db: AsyncSession, tenant_id: str, journal_code: str, move_date: date) -> str:
    prefix = f"{journal_code}/{move_date.year}/{move_date.month:02d}/"
    n = (
        await db.scalar(
            select(func.count(AccMove.id)).where(
                AccMove.tenant_id == tenant_id,
                AccMove.name.like(f"{prefix}%"),
            )
        )
        or 0
    )
    return f"{prefix}{int(n) + 1:04d}"


async def post_move(
    db: AsyncSession,
    *,
    tenant_id: str,
    branch_id: str | None,
    journal_code: str,
    move_type: str,
    move_date: date,
    partner_id: str | None,
    partner_name: str,
    ref: str,
    narration: str,
    lines: list[MoveLineIn],
    source_type: str,
    source_id: str | None,
    amount_total: float | None = None,
    posted_by: str | None = None,
    mirror_journal: bool = True,
) -> AccMove:
    if not lines:
        raise AccPostingError("Move must have lines")

    accounts = await db.execute(select(LedgerAccount).where(LedgerAccount.tenant_id == tenant_id))
    by_code = {a.code: a for a in accounts.scalars().all()}
    journals = await ensure_default_journals(db, tenant_id)
    journal = (
        journals.get(journal_code)
        or journals.get("PUR")
        or journals.get("GEN")
        or (next(iter(journals.values())) if journals else None)
    )

    total_debit = round(sum(l.debit for l in lines), 2)
    total_credit = round(sum(l.credit for l in lines), 2)
    if abs(total_debit - total_credit) > 0.01:
        raise AccPostingError(f"Move not balanced: debit {total_debit} != credit {total_credit}")

    move = AccMove(
        tenant_id=tenant_id,
        branch_id=branch_id,
        journal_id=journal.id if journal else None,
        name="/",
        move_type=move_type,
        state="posted",
        date=move_date,
        invoice_date=move_date,
        partner_id=partner_id,
        partner_name=partner_name or "",
        ref=ref,
        narration=narration,
        amount_total=amount_total if amount_total is not None else total_debit,
        amount_residual=amount_total if amount_total is not None else total_debit,
        source_type=source_type,
        source_id=source_id,
        posted_at=datetime.now(UTC),
        posted_by=posted_by,
    )
    db.add(move)
    await db.flush()
    move.name = await _next_move_name(db, tenant_id, journal.code if journal else "GEN", move_date)

    for ln in lines:
        acct = by_code.get(ln.account_code)
        if not acct:
            raise AccPostingError(f"Unknown account {ln.account_code}")
        residual = 0.0
        if acct.account_type in ("asset", "liability") and acct.code in ("1200", "2000"):
            residual = round(ln.debit - ln.credit, 2) if ln.debit else round(ln.credit - ln.debit, 2)
        db.add(
            AccMoveLine(
                move_id=move.id,
                account_id=acct.id,
                partner_id=ln.partner_id,
                partner_name=ln.partner_name or partner_name or "",
                name=ln.name,
                debit=round(ln.debit, 2),
                credit=round(ln.credit, 2),
                display_type=ln.display_type,
                amount_residual=max(0.0, abs(residual)),
            )
        )

    if mirror_journal:
        entry = JournalEntry(
            tenant_id=tenant_id,
            branch_id=branch_id,
            entry_date=move_date,
            reference=move.name,
            memo=narration,
            source=source_type or "acc_move",
            source_id=source_id or move.id,
        )
        db.add(entry)
        await db.flush()
        for ln in lines:
            acct = by_code.get(ln.account_code)
            if acct:
                db.add(
                    JournalLine(
                        entry_id=entry.id,
                        account_id=acct.id,
                        label=ln.name,
                        debit=round(ln.debit, 2),
                        credit=round(ln.credit, 2),
                    )
                )

    await db.flush()
    return move


async def register_vendor_payment_move(
    db: AsyncSession,
    *,
    tenant_id: str,
    branch_id: str | None,
    bill_id: str,
    partner_id: str | None,
    partner_name: str,
    amount: float,
    reference: str,
    journal_code: str = "CSH",
) -> AccPayment:
    pay = AccPayment(
        tenant_id=tenant_id,
        branch_id=branch_id,
        payment_type="outbound",
        partner_type="supplier",
        partner_id=partner_id,
        partner_name=partner_name,
        amount=amount,
        date=date.today(),
        bill_id=bill_id,
        reference=reference,
        state="posted",
    )
    db.add(pay)
    await db.flush()

    move = await post_move(
        db,
        tenant_id=tenant_id,
        branch_id=branch_id,
        journal_code=journal_code,
        move_type="entry",
        move_date=date.today(),
        partner_id=partner_id,
        partner_name=partner_name,
        ref=reference,
        narration=f"Vendor payment — {partner_name}",
        lines=[
            MoveLineIn("2000", "Accounts Payable", debit=amount, credit=0, partner_id=partner_id, partner_name=partner_name),
            MoveLineIn("1000", "Cash & Mobile Money", debit=0, credit=amount),
        ],
        source_type="vendor_payment",
        source_id=bill_id,
        amount_total=amount,
        mirror_journal=True,
    )
    pay.move_id = move.id

    if bill_id:
        bill_move = await db.scalar(
            select(AccMove.id).where(AccMove.source_type == "vendor_bill", AccMove.source_id == bill_id)
        )
        if bill_move:
            ap_lines = (
                await db.execute(
                    select(AccMoveLine, LedgerAccount)
                    .join(LedgerAccount, LedgerAccount.id == AccMoveLine.account_id)
                    .where(
                        AccMoveLine.move_id == bill_move,
                        LedgerAccount.code == "2000",
                    )
                )
            ).all()
            remaining = round(amount, 2)
            for ap_line, _acct in ap_lines:
                if remaining <= 0:
                    break
                cur = float(ap_line.amount_residual or 0)
                if cur <= 0:
                    cur = float(ap_line.credit or 0) - float(ap_line.debit or 0)
                take = min(cur, remaining)
                ap_line.amount_residual = round(max(0.0, cur - take), 2)
                remaining = round(remaining - take, 2)

    await db.flush()
    return pay
