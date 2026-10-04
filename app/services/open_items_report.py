"""Open items (Zalongwa / OCA open_items) from receivable & payable move lines."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.accounting import AccMove, AccMoveLine, LedgerAccount


async def build_open_items(
    db: AsyncSession,
    *,
    tenant_id: str,
    at_date: date,
    partner_type: str = "all",  # customer | supplier | all
) -> list[dict]:
    accounts = {
        a.id: a
        for a in (await db.execute(select(LedgerAccount).where(LedgerAccount.tenant_id == tenant_id))).scalars()
    }
    receivable_codes = {"1200"}
    payable_codes = {"2000"}

    q = (
        select(AccMoveLine, AccMove)
        .join(AccMove, AccMove.id == AccMoveLine.move_id)
        .where(AccMove.tenant_id == tenant_id, AccMove.state == "posted", AccMove.date <= at_date)
    )
    items: list[dict] = []
    for line, move in (await db.execute(q)).all():
        acct = accounts.get(line.account_id)
        if not acct:
            continue
        is_rec = acct.code in receivable_codes
        is_pay = acct.code in payable_codes
        if partner_type == "customer" and not is_rec:
            continue
        if partner_type == "supplier" and not is_pay:
            continue
        if partner_type == "all" and not (is_rec or is_pay):
            continue
        residual = float(line.amount_residual or 0)
        if residual <= 0 and is_pay:
            residual = max(0.0, float(line.credit or 0) - float(line.debit or 0))
        if residual <= 0 and is_rec:
            residual = max(0.0, float(line.debit or 0) - float(line.credit or 0))
        if residual <= 0.01:
            continue
        items.append(
            {
                "date": move.date.isoformat(),
                "move_name": move.name,
                "partner_name": line.partner_name or move.partner_name,
                "label": line.name,
                "account_code": acct.code,
                "amount": round(residual, 2),
                "partner_type": "customer" if is_rec else "supplier",
            }
        )
    return sorted(items, key=lambda x: x["date"])
