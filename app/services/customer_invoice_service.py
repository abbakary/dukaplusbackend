"""Customer invoices as account.move (out_invoice) with smart-button payloads."""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Sale
from app.models.accounting import AccMove, AccMoveLine, AccPayment, LedgerAccount
from app.services.acc_posting_service import MoveLineIn, post_move


async def list_customer_invoices(
    db: AsyncSession,
    *,
    tenant_id: str,
    limit: int = 100,
) -> list[dict]:
    q = (
        select(AccMove)
        .where(
            AccMove.tenant_id == tenant_id,
            AccMove.move_type.in_(("out_invoice", "out_refund")),
            AccMove.state == "posted",
        )
        .order_by(AccMove.date.desc())
        .limit(limit)
    )
    moves = (await db.execute(q)).scalars().all()
    out: list[dict] = []
    for m in moves:
        pay_count = int(
            await db.scalar(
                select(func.count(AccPayment.id)).where(
                    AccPayment.tenant_id == tenant_id,
                    AccPayment.partner_id == m.partner_id,
                    AccPayment.payment_type == "inbound",
                )
            )
            or 0
        )
        out.append(
            {
                "id": m.id,
                "name": m.name,
                "move_type": m.move_type,
                "partner_name": m.partner_name,
                "partner_id": m.partner_id,
                "date": m.date.isoformat(),
                "amount_total": m.amount_total,
                "amount_residual": m.amount_residual,
                "payment_state": m.payment_state,
                "source_type": m.source_type,
                "source_id": m.source_id,
                "smart_buttons": {"payments": pay_count},
            }
        )
    return out


async def get_customer_invoice_detail(db: AsyncSession, *, tenant_id: str, move_id: str) -> dict | None:
    move = await db.get(AccMove, move_id)
    if not move or move.tenant_id != tenant_id:
        return None
    accounts = {
        a.id: a for a in (await db.execute(select(LedgerAccount).where(LedgerAccount.tenant_id == tenant_id))).scalars()
    }
    lines = (await db.execute(select(AccMoveLine).where(AccMoveLine.move_id == move_id))).scalars().all()
    journal_items = []
    for ln in lines:
        acct = accounts.get(ln.account_id)
        journal_items.append(
            {
                "account_code": acct.code if acct else "",
                "account_name": acct.name if acct else "",
                "label": ln.name,
                "debit": ln.debit,
                "credit": ln.credit,
                "partner_name": ln.partner_name,
            }
        )
    payments = (
        await db.execute(
            select(AccPayment).where(
                AccPayment.tenant_id == tenant_id,
                AccPayment.payment_type == "inbound",
                AccPayment.partner_id == move.partner_id,
            )
        )
    ).scalars().all()
    return {
        "id": move.id,
        "name": move.name,
        "move_type": move.move_type,
        "state": move.state,
        "payment_state": move.payment_state,
        "partner_name": move.partner_name,
        "partner_id": move.partner_id,
        "date": move.date.isoformat(),
        "due_date": move.due_date.isoformat() if move.due_date else None,
        "amount_total": move.amount_total,
        "amount_residual": move.amount_residual,
        "ref": move.ref,
        "journal_items": journal_items,
        "payments": [
            {"id": p.id, "amount": p.amount, "date": p.date.isoformat(), "reference": p.reference} for p in payments
        ],
        "payments_count": len(payments),
        "smart_buttons": {"payments": len(payments)},
    }


async def ensure_sale_invoice_move(db: AsyncSession, *, tenant_id: str, sale: Sale) -> AccMove | None:
    if sale.acc_move_id:
        return await db.get(AccMove, sale.acc_move_id)
    existing = await db.scalar(
        select(AccMove.id).where(
            AccMove.tenant_id == tenant_id,
            AccMove.source_type == "pos_sale",
            AccMove.source_id == sale.id,
        )
    )
    if existing:
        sale.acc_move_id = existing
        await db.flush()
        return await db.get(AccMove, existing)
    return None


async def register_customer_payment_move(
    db: AsyncSession,
    *,
    tenant_id: str,
    branch_id: str | None,
    move_id: str,
    partner_id: str | None,
    partner_name: str,
    amount: float,
    reference: str,
    journal_code: str = "CSH",
) -> AccPayment:
    pay = AccPayment(
        tenant_id=tenant_id,
        branch_id=branch_id,
        payment_type="inbound",
        partner_type="customer",
        partner_id=partner_id,
        partner_name=partner_name,
        amount=amount,
        date=date.today(),
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
        narration=f"Customer payment — {partner_name}",
        lines=[
            MoveLineIn("1000", "Cash & Mobile Money", debit=amount),
            MoveLineIn(
                "1200",
                "Accounts Receivable",
                credit=amount,
                partner_id=partner_id,
                partner_name=partner_name,
            ),
        ],
        source_type="customer_payment",
        source_id=move_id,
        amount_total=amount,
        mirror_journal=True,
    )
    pay.move_id = move.id

    inv = await db.get(AccMove, move_id)
    if inv:
        inv.amount_residual = round(max(0.0, float(inv.amount_residual or 0) - amount), 2)
        inv.payment_state = "paid" if inv.amount_residual <= 0.01 else "partial"
        ar_lines = (
            await db.execute(
                select(AccMoveLine, LedgerAccount)
                .join(LedgerAccount, LedgerAccount.id == AccMoveLine.account_id)
                .where(AccMoveLine.move_id == move_id, LedgerAccount.code == "1200")
            )
        ).all()
        remaining = round(amount, 2)
        for ar_line, _ in ar_lines:
            if remaining <= 0:
                break
            cur = float(ar_line.amount_residual or 0)
            if cur <= 0:
                cur = float(ar_line.debit or 0) - float(ar_line.credit or 0)
            take = min(cur, remaining)
            ar_line.amount_residual = round(max(0.0, cur - take), 2)
            if ar_line.amount_residual <= 0.01:
                ar_line.reconciled = True
            remaining = round(remaining - take, 2)

    await db.flush()
    return pay
