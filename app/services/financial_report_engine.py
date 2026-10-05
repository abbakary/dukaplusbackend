"""Financial statement hierarchy (TZ retail template)."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.branch_scope import branch_id_filter
from app.models.accounting import AccFinancialReportLine, AccMove, AccMoveLine, LedgerAccount
from app.services.branch_service import get_tenant_default_branch_id

TZ_PNL_SEED = [
    ("root", None, 0, "Profit and Loss (TZ)", "sum", "", "", 1, "title"),
    ("rev", "root", 1, "Revenue", "account_type", "", "income", 1, "section"),
    ("cogs", "root", 2, "Cost of goods sold", "account_type", "", "expense", -1, "normal"),
    ("opex", "root", 3, "Operating expenses", "accounts", "6000,6100,6200,6300", "", 1, "normal"),
    ("net", "root", 99, "Net profit", "sum", "", "", 1, "total"),
]

TZ_BS_SEED = [
    ("root", None, 0, "Balance Sheet (TZ)", "sum", "", "", 1, "title"),
    ("assets", "root", 1, "Assets", "sum", "", "", 1, "section"),
    ("assets_cur", "assets", 1, "Current assets", "accounts", "1000,1100,1200,1300,1310", "", 1, "normal"),
    ("liab", "root", 2, "Liabilities", "sum", "", "", -1, "section"),
    ("liab_cur", "liab", 1, "Current liabilities", "accounts", "2000,2100,2200", "", -1, "normal"),
    ("equity", "root", 3, "Equity", "account_type", "", "equity", -1, "section"),
]


async def ensure_financial_report_template(db: AsyncSession, tenant_id: str, report_code: str) -> None:
    existing = await db.scalar(
        select(AccFinancialReportLine.id).where(
            AccFinancialReportLine.tenant_id == tenant_id,
            AccFinancialReportLine.report_code == report_code,
        )
    )
    if existing:
        return
    seed = TZ_PNL_SEED if report_code == "profit_and_loss" else TZ_BS_SEED
    id_by_key: dict[str, str] = {}
    for key, parent_key, seq, name, ltype, codes, types, sign, style in seed:
        parent_id = id_by_key.get(parent_key) if parent_key else None
        row = AccFinancialReportLine(
            tenant_id=tenant_id,
            report_code=report_code,
            parent_id=parent_id,
            sequence=seq,
            name=name,
            line_type=ltype,
            account_codes=codes,
            account_types=types,
            sign=sign,
            style=style,
        )
        db.add(row)
        await db.flush()
        id_by_key[key] = row.id


async def _balance_by_account(
    db: AsyncSession,
    tenant_id: str,
    date_from: date,
    date_to: date,
    branch_id: str | None = None,
) -> dict[str, float]:
    accounts = {a.id: a for a in (await db.execute(select(LedgerAccount).where(LedgerAccount.tenant_id == tenant_id))).scalars()}
    hq_id = await get_tenant_default_branch_id(db, tenant_id) if branch_id else None
    q = (
        select(AccMoveLine, AccMove)
        .join(AccMove, AccMove.id == AccMoveLine.move_id)
        .where(AccMove.tenant_id == tenant_id, AccMove.state == "posted")
    )
    clause = branch_id_filter(AccMove.branch_id, branch_id, hq_id)
    if clause is not None:
        q = q.where(clause)
    totals: dict[str, float] = {}
    for line, move in (await db.execute(q)).all():
        if move.date < date_from or move.date > date_to:
            continue
        acct = accounts.get(line.account_id)
        if not acct:
            continue
        totals[acct.code] = totals.get(acct.code, 0.0) + float(line.debit or 0) - float(line.credit or 0)
    return totals


async def compute_financial_report(
    db: AsyncSession,
    *,
    tenant_id: str,
    report_code: str,
    date_from: date,
    date_to: date,
    branch_id: str | None = None,
) -> dict:
    await ensure_financial_report_template(db, tenant_id, report_code)
    if report_code == "balance_sheet":
        await ensure_financial_report_template(db, tenant_id, "balance_sheet")

    lines = (
        await db.execute(
            select(AccFinancialReportLine)
            .where(AccFinancialReportLine.tenant_id == tenant_id, AccFinancialReportLine.report_code == report_code)
            .order_by(AccFinancialReportLine.sequence)
        )
    ).scalars().all()
    accounts = {a.code: a for a in (await db.execute(select(LedgerAccount).where(LedgerAccount.tenant_id == tenant_id))).scalars()}
    balances = await _balance_by_account(db, tenant_id, date_from, date_to, branch_id)

    def line_amount(row: AccFinancialReportLine) -> float:
        if row.line_type == "accounts":
            codes = [c.strip() for c in row.account_codes.split(",") if c.strip()]
            return sum(balances.get(c, 0.0) for c in codes) * row.sign
        if row.line_type == "account_type":
            types = [t.strip() for t in row.account_types.split(",") if t.strip()]
            s = 0.0
            for code, bal in balances.items():
                acct = accounts.get(code)
                if acct and acct.account_type in types:
                    s += bal
            return s * row.sign
        return 0.0

    raw_amount: dict[str, float] = {}
    children_of: dict[str | None, list[str]] = {}
    for row in lines:
        children_of.setdefault(row.parent_id, []).append(row.id)
        if row.line_type != "sum":
            raw_amount[row.id] = line_amount(row)
        elif row.name.startswith("Net"):
            raw_amount[row.id] = sum(line_amount(r) for r in lines if r.line_type != "sum")
        else:
            raw_amount[row.id] = 0.0

    def sum_children(line_id: str) -> float:
        total = 0.0
        for cid in children_of.get(line_id, []):
            child = next((r for r in lines if r.id == cid), None)
            if not child:
                continue
            if child.line_type == "sum":
                total += sum_children(cid)
            else:
                total += raw_amount.get(cid, 0.0)
        return total

    for row in lines:
        if row.line_type == "sum" and not row.name.startswith("Net"):
            raw_amount[row.id] = sum_children(row.id)

    out_lines = []
    for row in lines:
        depth = 0
        pid = row.parent_id
        while pid:
            depth += 1
            parent = next((r for r in lines if r.id == pid), None)
            pid = parent.parent_id if parent else None
        out_lines.append(
            {
                "id": row.id,
                "name": row.name,
                "level": depth,
                "style": row.style,
                "amount": round(raw_amount.get(row.id, 0.0), 2),
                "line_type": row.line_type,
            }
        )
    return {"report_code": report_code, "date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "lines": out_lines}
