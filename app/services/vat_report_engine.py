"""VAT report from posted move lines (output / input / net payable)."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Sale
from app.models.accounting import AccMove, AccMoveLine, LedgerAccount

from app.services.accounting_reports import COMPLETED_SALE_STATUSES

VAT_OUTPUT_CODES = frozenset({"2100"})
VAT_INPUT_CODES = frozenset({"1310", "1300"})


async def build_vat_report(
    db: AsyncSession,
    *,
    tenant_id: str,
    date_from: date,
    date_to: date,
    tax_detail: bool = False,
) -> dict:
    accounts = {
        a.id: a
        for a in (await db.execute(select(LedgerAccount).where(LedgerAccount.tenant_id == tenant_id))).scalars()
    }
    q = (
        select(AccMoveLine, AccMove)
        .join(AccMove, AccMove.id == AccMoveLine.move_id)
        .where(AccMove.tenant_id == tenant_id, AccMove.state == "posted")
    )
    output = 0.0
    input_vat = 0.0
    detail: list[dict] = []
    for line, move in (await db.execute(q)).all():
        if move.date < date_from or move.date > date_to:
            continue
        acct = accounts.get(line.account_id)
        if not acct:
            continue
        net = float(line.credit or 0) - float(line.debit or 0)
        if acct.code in VAT_OUTPUT_CODES:
            output += net
            if tax_detail:
                detail.append(
                    {
                        "tag": "output",
                        "move_name": move.name,
                        "date": move.date.isoformat(),
                        "label": line.name,
                        "amount": round(net, 2),
                    }
                )
        elif acct.code in VAT_INPUT_CODES:
            input_vat += float(line.debit or 0) - float(line.credit or 0)
            if tax_detail:
                detail.append(
                    {
                        "tag": "input",
                        "move_name": move.name,
                        "date": move.date.isoformat(),
                        "label": line.name,
                        "amount": round(float(line.debit or 0) - float(line.credit or 0), 2),
                    }
                )

    sales_std = 0.0
    sales_exempt = 0.0
    sales_zero = 0.0
    if abs(output) < 0.01 and abs(input_vat) < 0.01:
        q = select(Sale).where(
            Sale.tenant_id == tenant_id,
            Sale.status.in_(tuple(COMPLETED_SALE_STATUSES)),
        )
        for sale in (await db.execute(q)).scalars().all():
            if sale.created_at:
                sd = sale.created_at.date()
                if sd < date_from or sd > date_to:
                    continue
            vat = float(sale.vat_amount or 0)
            total = float(sale.total or 0)
            if vat > 0:
                sales_std += total - vat
                output += vat
            elif total > 0:
                sales_zero += total

    net_payable = round(output - input_vat, 2)
    rows = [
        {"label_en": "Standard-rated sales (net)", "label_sw": "Mauzo ya kawaida (neto)", "amount": round(sales_std, 2)},
        {"label_en": "Zero-rated sales", "label_sw": "Mauzo zero-rated", "amount": round(sales_zero, 2)},
        {"label_en": "Exempt sales (est.)", "label_sw": "Mauzo yasiyo na VAT", "amount": round(sales_exempt, 2)},
        {"label_en": "Output VAT (sales)", "label_sw": "VAT mauzo", "amount": round(output, 2)},
        {"label_en": "Input VAT (purchases)", "label_sw": "VAT manunuzi", "amount": round(input_vat, 2)},
        {"label_en": "Net VAT payable", "label_sw": "VAT neto kulipa", "amount": net_payable},
    ]
    return {"rows": rows, "detail": detail, "net_vat": net_payable}
