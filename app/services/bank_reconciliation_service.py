"""Bank statement import & reconciliation (Odoo account.bank.statement lite)."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.accounting import AccBankStatement, AccBankStatementLine, AccJournal, AccMoveLine, LedgerAccount


async def list_statements(db: AsyncSession, *, tenant_id: str) -> list[dict]:
    rows = (
        await db.execute(
            select(AccBankStatement)
            .where(AccBankStatement.tenant_id == tenant_id)
            .order_by(AccBankStatement.date_to.desc())
        )
    ).scalars().all()
    return [
        {
            "id": s.id,
            "name": s.name,
            "date_from": s.date_from.isoformat(),
            "date_to": s.date_to.isoformat(),
            "balance_start": s.balance_start,
            "balance_end": s.balance_end,
            "state": s.state,
        }
        for s in rows
    ]


async def create_statement(
    db: AsyncSession,
    *,
    tenant_id: str,
    name: str,
    date_from: date,
    date_to: date,
    balance_start: float = 0,
    balance_end: float = 0,
) -> AccBankStatement:
    journal = await db.scalar(
        select(AccJournal).where(AccJournal.tenant_id == tenant_id, AccJournal.code == "BNK")
    )
    st = AccBankStatement(
        tenant_id=tenant_id,
        journal_id=journal.id if journal else None,
        name=name,
        date_from=date_from,
        date_to=date_to,
        balance_start=balance_start,
        balance_end=balance_end,
        state="open",
    )
    db.add(st)
    await db.flush()
    return st


async def add_statement_line(
    db: AsyncSession,
    *,
    statement_id: str,
    line_date: date,
    payment_ref: str,
    partner_name: str,
    amount: float,
) -> AccBankStatementLine:
    ln = AccBankStatementLine(
        statement_id=statement_id,
        date=line_date,
        payment_ref=payment_ref,
        partner_name=partner_name,
        amount=round(amount, 2),
    )
    db.add(ln)
    await db.flush()
    return ln


async def statement_lines(db: AsyncSession, *, statement_id: str) -> list[dict]:
    rows = (
        await db.execute(
            select(AccBankStatementLine).where(AccBankStatementLine.statement_id == statement_id).order_by(AccBankStatementLine.date)
        )
    ).scalars().all()
    return [
        {
            "id": r.id,
            "date": r.date.isoformat(),
            "payment_ref": r.payment_ref,
            "partner_name": r.partner_name,
            "amount": r.amount,
            "is_reconciled": r.is_reconciled,
            "move_line_id": r.move_line_id,
        }
        for r in rows
    ]


async def reconcile_line(
    db: AsyncSession,
    *,
    tenant_id: str,
    statement_line_id: str,
    move_line_id: str,
) -> None:
    st_line = await db.get(AccBankStatementLine, statement_line_id)
    if not st_line:
        raise ValueError("Statement line not found")
    mv_line = await db.get(AccMoveLine, move_line_id)
    if not mv_line:
        raise ValueError("Move line not found")
    st_line.move_line_id = move_line_id
    st_line.is_reconciled = True
    mv_line.reconciled = True
    await db.flush()


async def suggest_move_lines(db: AsyncSession, *, tenant_id: str, amount: float, limit: int = 20) -> list[dict]:
    """Unreconciled cash/bank move lines near amount."""
    cash_accounts = (
        await db.execute(
            select(LedgerAccount.id).where(
                LedgerAccount.tenant_id == tenant_id,
                LedgerAccount.code.in_(("1000", "1100")),
            )
        )
    ).scalars().all()
    if not cash_accounts:
        return []
    q = (
        select(AccMoveLine)
        .where(
            AccMoveLine.account_id.in_(cash_accounts),
            AccMoveLine.reconciled.is_(False),
        )
        .limit(limit)
    )
    lines = (await db.execute(q)).scalars().all()
    out = []
    for ln in lines:
        net = float(ln.debit or 0) - float(ln.credit or 0)
        if abs(abs(net) - abs(amount)) <= max(1.0, abs(amount) * 0.05):
            out.append(
                {
                    "id": ln.id,
                    "name": ln.name,
                    "debit": ln.debit,
                    "credit": ln.credit,
                    "partner_name": ln.partner_name,
                }
            )
    return out
