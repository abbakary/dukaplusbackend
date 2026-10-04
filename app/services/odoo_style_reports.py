"""Odoo-style accounting reports (filters + structured output for UI/PDF)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.branch_scope import branch_id_filter
from app.models import Sale
from app.models.accounting import AccMove, AccMoveLine, JournalEntry, JournalLine, LedgerAccount
from app.services.accounting_defaults import ensure_default_chart
from app.services.accounting_reports import COMPLETED_SALE_STATUSES, build_report_bundle
from app.services.branch_service import get_tenant_default_branch_id
from app.services.financial_report_engine import compute_financial_report
from app.services.open_items_report import build_open_items
from app.services.vat_report_engine import build_vat_report

REPORT_CATALOG: list[dict[str, Any]] = [
    {"key": "profit_and_loss", "group": "statement", "name_en": "Profit and Loss", "name_sw": "Mapato na hasara"},
    {"key": "balance_sheet", "group": "statement", "name_en": "Balance Sheet", "name_sw": "Mizania"},
    {"key": "cash_flow", "group": "statement", "name_en": "Cash Flow Statement", "name_sw": "Mtiririko wa fedha"},
    {"key": "trial_balance", "group": "ledger", "name_en": "Trial Balance", "name_sw": "Mizani ya majaribio"},
    {"key": "general_ledger", "group": "ledger", "name_en": "General Ledger", "name_sw": "Leja kuu"},
    {"key": "journal_report", "group": "audit", "name_en": "Journal Report", "name_sw": "Ripoti ya jarida"},
    {"key": "partner_ledger", "group": "partner", "name_en": "Partner Ledger", "name_sw": "Leja ya washirika"},
    {"key": "aged_receivable", "group": "partner", "name_en": "Aged Receivable", "name_sw": "Madeni wateja"},
    {"key": "aged_payable", "group": "partner", "name_en": "Aged Payable", "name_sw": "Madeni wasambazaji"},
    {"key": "tax_report", "group": "tax", "name_en": "Tax Report", "name_sw": "Ripoti ya kodi"},
    {"key": "vat_report", "group": "tax", "name_en": "VAT Report (Zalongwa)", "name_sw": "Ripoti ya VAT"},
    {"key": "open_items", "group": "partner", "name_en": "Open Items", "name_sw": "Vitu vilivyobaki"},
    {"key": "invoice_analysis", "group": "management", "name_en": "Invoice Analysis", "name_sw": "Uchambuzi wa ankara"},
    {"key": "account_analysis", "group": "management", "name_en": "Account Analysis", "name_sw": "Uchambuzi wa akaunti"},
]


def _parse_dates(date_from: date | None, date_to: date | None) -> tuple[date, date]:
    today = date.today()
    d_to = date_to or today
    d_from = date_from or date(d_to.year, d_to.month, 1)
    if d_from > d_to:
        d_from, d_to = d_to, d_from
    return d_from, d_to


async def _journal_lines_query(
    db: AsyncSession,
    tenant_id: str,
    branch_id: str | None,
    date_from: date | None,
    date_to: date | None,
    *,
    before_date: date | None = None,
):
    hq_id = await get_tenant_default_branch_id(db, tenant_id) if branch_id else None
    accounts = await ensure_default_chart(db, tenant_id)
    acct_by_id = {a.id: a for a in accounts}

    q = (
        select(JournalLine, JournalEntry)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(JournalEntry.tenant_id == tenant_id)
    )
    clause = branch_id_filter(JournalEntry.branch_id, branch_id, hq_id)
    if clause is not None:
        q = q.where(clause)

    rows: list[tuple[JournalLine, JournalEntry, LedgerAccount]] = []
    for line, entry in (await db.execute(q)).all():
        acct = acct_by_id.get(line.account_id)
        if not acct:
            continue
        ed = entry.entry_date
        if before_date is not None:
            if ed >= before_date:
                continue
        elif date_from is not None and date_to is not None:
            if ed < date_from or ed > date_to:
                continue
        rows.append((line, entry, acct))
    return rows


async def _acc_move_lines_query(
    db: AsyncSession,
    tenant_id: str,
    branch_id: str | None,
    date_from: date | None,
    date_to: date | None,
    *,
    before_date: date | None = None,
    target_move: str = "posted",
):
    hq_id = await get_tenant_default_branch_id(db, tenant_id) if branch_id else None
    accounts = await ensure_default_chart(db, tenant_id)
    acct_by_id = {a.id: a for a in accounts}

    q = (
        select(AccMoveLine, AccMove)
        .join(AccMove, AccMove.id == AccMoveLine.move_id)
        .where(AccMove.tenant_id == tenant_id)
    )
    if target_move == "posted":
        q = q.where(AccMove.state == "posted")
    clause = branch_id_filter(AccMove.branch_id, branch_id, hq_id)
    if clause is not None:
        q = q.where(clause)

    rows: list[tuple[AccMoveLine, AccMove, LedgerAccount]] = []
    for line, move in (await db.execute(q)).all():
        acct = acct_by_id.get(line.account_id)
        if not acct:
            continue
        md = move.date
        if before_date is not None:
            if md >= before_date:
                continue
        elif date_from is not None and date_to is not None:
            if md < date_from or md > date_to:
                continue
        rows.append((line, move, acct))
    return rows


async def _tenant_has_acc_moves(db: AsyncSession, tenant_id: str) -> bool:
    row = await db.scalar(select(AccMove.id).where(AccMove.tenant_id == tenant_id).limit(1))
    return row is not None


def _financial_lines_total(fin: dict[str, Any] | None) -> float:
    if not fin:
        return 0.0
    lines = fin.get("lines") or []
    return sum(abs(float(l.get("amount") or 0)) for l in lines)


async def run_odoo_report(
    db: AsyncSession,
    *,
    report_key: str,
    tenant_id: str,
    branch_id: str | None,
    books_mode: str = "standard",
    date_from: date | None = None,
    date_to: date | None = None,
    target_move: str = "posted",
) -> dict[str, Any]:
    d_from, d_to = _parse_dates(date_from, date_to)
    meta = {
        "report_key": report_key,
        "date_from": d_from.isoformat(),
        "date_to": d_to.isoformat(),
        "target_move": target_move,
        "currency": "TZS",
        "books_mode": books_mode,
    }

    if report_key in ("profit_and_loss", "balance_sheet", "cash_flow", "aged_receivable", "aged_payable", "tax_report"):
        bundle = await build_report_bundle(
            db,
            tenant_id=tenant_id,
            branch_id=branch_id,
            books_mode=books_mode,
            date_from=d_from,
            date_to=d_to,
        )
        fin: dict[str, Any] | None = None
        if report_key in ("profit_and_loss", "balance_sheet") and await _tenant_has_acc_moves(db, tenant_id):
            fin = await compute_financial_report(
                db,
                tenant_id=tenant_id,
                report_code=report_key,
                date_from=d_from,
                date_to=d_to,
                branch_id=branch_id,
            )
        if report_key == "profit_and_loss":
            inc = bundle["income_statement"]
            if fin and _financial_lines_total(fin) > 0.01:
                return {
                    **meta,
                    "type": "hierarchy",
                    "data": {**inc, **fin},
                    "lines": fin.get("lines") or [],
                    "source": "ledger",
                }
            return {
                **meta,
                "type": "hierarchy",
                "data": inc,
                "lines": inc.get("lines") or [],
                "source": "operations",
            }
        if report_key == "balance_sheet":
            bs = bundle["balance_sheet"]
            if fin and _financial_lines_total(fin) > 0.01:
                return {
                    **meta,
                    "type": "hierarchy",
                    "data": {**bs, **fin},
                    "lines": fin.get("lines") or [],
                    "source": "ledger",
                }
            return {**meta, "type": "balance_sheet", "data": bs, "source": "operations"}
        if report_key == "cash_flow":
            return {**meta, "type": "cash_flow", "data": bundle.get("cash_flow") or {}}
        if report_key == "aged_receivable":
            return {**meta, "type": "aged", "partner_type": "customer", "rows": bundle["aged_receivables"]}
        if report_key == "aged_payable":
            return {**meta, "type": "aged", "partner_type": "vendor", "rows": bundle["aged_payables"]}
        if report_key == "tax_report":
            inc = bundle["income_statement"]
            return {
                **meta,
                "type": "tax",
                "rows": [
                    {"label_en": "Output VAT", "label_sw": "VAT mauzo", "amount": inc.get("vat_output", 0)},
                    {"label_en": "Net operating (excl. VAT)", "label_sw": "Uendeshaji", "amount": inc.get("net_before_tax", 0)},
                ],
            }

    if report_key == "open_items":
        partner_type = "all"
        rows = await build_open_items(db, tenant_id=tenant_id, at_date=d_to, partner_type=partner_type)
        total = round(sum(float(r.get("amount") or 0) for r in rows), 2)
        return {**meta, "type": "open_items", "rows": rows, "totals": {"open": total}}

    if report_key == "vat_report":
        vat = await build_vat_report(db, tenant_id=tenant_id, date_from=d_from, date_to=d_to, tax_detail=True)
        return {
            **meta,
            "type": "vat",
            "rows": vat["rows"],
            "detail": vat.get("detail") or [],
            "net_vat": vat.get("net_vat", 0),
            "wizard": {"based_on": "taxtags", "target_move": target_move, "tax_detail": True},
        }

    if report_key == "trial_balance":
        use_moves = await _tenant_has_acc_moves(db, tenant_id)
        if use_moves:
            initial_rows = await _acc_move_lines_query(
                db, tenant_id, branch_id, None, None, before_date=d_from, target_move=target_move
            )
            period_rows = await _acc_move_lines_query(
                db, tenant_id, branch_id, d_from, d_to, target_move=target_move
            )
        else:
            initial_rows = await _journal_lines_query(db, tenant_id, branch_id, None, None, before_date=d_from)
            period_rows = await _journal_lines_query(db, tenant_id, branch_id, d_from, d_to)
        totals: dict[str, dict] = {}

        def add(rows, field: str):
            for line, _entry, acct in rows:
                b = totals.setdefault(
                    acct.code,
                    {"code": acct.code, "name": acct.name, "initial_balance": 0.0, "debit": 0.0, "credit": 0.0},
                )
                if field == "initial":
                    b["initial_balance"] += float(line.debit or 0) - float(line.credit or 0)
                else:
                    b["debit"] += float(line.debit or 0)
                    b["credit"] += float(line.credit or 0)

        add(initial_rows, "initial")
        add(period_rows, "period")
        out = []
        for code in sorted(totals.keys()):
            t = totals[code]
            end = round(t["initial_balance"] + t["debit"] - t["credit"], 2)
            out.append(
                {
                    **t,
                    "initial_balance": round(t["initial_balance"], 2),
                    "debit": round(t["debit"], 2),
                    "credit": round(t["credit"], 2),
                    "end_balance": end,
                }
            )
        td = round(sum(r["debit"] for r in out), 2)
        tc = round(sum(r["credit"] for r in out), 2)
        return {**meta, "type": "trial_balance", "rows": out, "totals": {"debit": td, "credit": tc}}

    if report_key == "general_ledger":
        use_moves = await _tenant_has_acc_moves(db, tenant_id)
        if use_moves:
            period_rows = await _acc_move_lines_query(
                db, tenant_id, branch_id, d_from, d_to, target_move=target_move
            )
        else:
            period_rows = await _journal_lines_query(db, tenant_id, branch_id, d_from, d_to)
        by_acct: dict[str, dict] = {}
        for line, entry, acct in period_rows:
            g = by_acct.setdefault(
                acct.code,
                {"code": acct.code, "name": acct.name, "debit": 0.0, "credit": 0.0, "lines": []},
            )
            g["debit"] += float(line.debit or 0)
            g["credit"] += float(line.credit or 0)
            ref_date = entry.date.isoformat() if hasattr(entry, "date") else entry.entry_date.isoformat()
            reference = entry.name if hasattr(entry, "name") else entry.reference
            label = line.name if hasattr(line, "name") else line.label
            source = getattr(entry, "source_type", None) or getattr(entry, "source", "")
            g["lines"].append(
                {
                    "date": ref_date,
                    "reference": reference,
                    "label": label,
                    "debit": float(line.debit or 0),
                    "credit": float(line.credit or 0),
                    "source": source,
                    "partner_name": getattr(line, "partner_name", "") or "",
                }
            )
        accounts = [by_acct[k] for k in sorted(by_acct.keys())]
        for a in accounts:
            a["debit"] = round(a["debit"], 2)
            a["credit"] = round(a["credit"], 2)
            a["balance"] = round(a["debit"] - a["credit"], 2)
        return {**meta, "type": "general_ledger", "accounts": accounts}

    if report_key == "journal_report":
        use_moves = await _tenant_has_acc_moves(db, tenant_id)
        if use_moves:
            period_rows = await _acc_move_lines_query(
                db, tenant_id, branch_id, d_from, d_to, target_move=target_move
            )
        else:
            period_rows = await _journal_lines_query(db, tenant_id, branch_id, d_from, d_to)
        entries_map: dict[str, dict] = {}
        for line, entry, acct in period_rows:
            e = entries_map.setdefault(
                entry.id,
                {
                    "id": entry.id,
                    "date": entry.date.isoformat() if hasattr(entry, "date") else entry.entry_date.isoformat(),
                    "reference": entry.name if hasattr(entry, "name") else entry.reference,
                    "memo": getattr(entry, "narration", None) or getattr(entry, "memo", ""),
                    "source": getattr(entry, "source_type", None) or getattr(entry, "source", ""),
                    "lines": [],
                },
            )
            e["lines"].append(
                {
                    "account_code": acct.code,
                    "account_name": acct.name,
                    "label": line.name if hasattr(line, "name") else line.label,
                    "debit": float(line.debit or 0),
                    "credit": float(line.credit or 0),
                    "partner_name": getattr(line, "partner_name", "") or "",
                }
            )
        journals = sorted(entries_map.values(), key=lambda x: (x["date"], x["reference"]))
        return {**meta, "type": "journal_report", "entries": journals}

    if report_key == "partner_ledger":
        bundle = await build_report_bundle(
            db,
            tenant_id=tenant_id,
            branch_id=branch_id,
            books_mode=books_mode,
            date_from=d_from,
            date_to=d_to,
        )
        return {
            **meta,
            "type": "partner_ledger",
            "customers": bundle["aged_receivables"],
            "vendors": bundle["aged_payables"],
        }

    if report_key == "invoice_analysis":
        hq_id = await get_tenant_default_branch_id(db, tenant_id) if branch_id else None
        q = select(Sale).where(
            Sale.tenant_id == tenant_id,
            Sale.status.in_(tuple(COMPLETED_SALE_STATUSES)),
        )
        clause = branch_id_filter(Sale.branch_id, branch_id, hq_id)
        if clause is not None:
            q = q.where(clause)
        sales = (await db.execute(q)).scalars().all()
        by_customer: dict[str, dict] = {}
        by_product: dict[str, dict] = {}
        for s in sales:
            if s.created_at and s.created_at.date() < d_from:
                continue
            if s.created_at and s.created_at.date() > d_to:
                continue
            cname = (s.customer_name or "Walk-in").strip()
            by_customer.setdefault(cname, {"name": cname, "count": 0, "total": 0.0})
            by_customer[cname]["count"] += 1
            by_customer[cname]["total"] += float(s.total or 0)
            for raw in s.items or []:
                item = raw if isinstance(raw, dict) else {}
                pname = str(item.get("product_name") or "Item")
                by_product.setdefault(pname, {"name": pname, "qty": 0.0, "total": 0.0})
                by_product[pname]["qty"] += float(item.get("quantity") or 0)
                by_product[pname]["total"] += float(item.get("total") or item.get("totalPrice") or 0)
        return {
            **meta,
            "type": "invoice_analysis",
            "by_customer": sorted(by_customer.values(), key=lambda x: x["total"], reverse=True)[:100],
            "by_product": sorted(by_product.values(), key=lambda x: x["total"], reverse=True)[:100],
        }

    if report_key == "account_analysis":
        accounts = await ensure_default_chart(db, tenant_id)
        use_moves = await _tenant_has_acc_moves(db, tenant_id)
        if use_moves:
            period_rows = await _acc_move_lines_query(
                db, tenant_id, branch_id, d_from, d_to, target_move=target_move
            )
            by_type: dict[str, float] = {}
            for line, _move, acct in period_rows:
                by_type[acct.account_type] = by_type.get(acct.account_type, 0.0) + float(line.debit or 0) - float(
                    line.credit or 0
                )
            return {
                **meta,
                "type": "account_analysis",
                "by_account_type": [{"type": k, "net": round(v, 2)} for k, v in sorted(by_type.items())],
                "account_count": len(accounts),
            }
        period_rows = await _journal_lines_query(db, tenant_id, branch_id, d_from, d_to)
        by_type: dict[str, float] = {}
        for line, _entry, acct in period_rows:
            by_type[acct.account_type] = by_type.get(acct.account_type, 0.0) + float(line.debit or 0) - float(
                line.credit or 0
            )
        return {
            **meta,
            "type": "account_analysis",
            "by_account_type": [{"type": k, "net": round(v, 2)} for k, v in sorted(by_type.items())],
            "account_count": len(accounts),
        }

    return {**meta, "type": "unknown", "error": f"Unknown report: {report_key}"}
