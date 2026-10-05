"""Accounting reports (filters + structured output for UI/PDF)."""

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
from app.services.fiscal_year import account_uses_fy_initial, fiscal_year_start
from app.services.vat_report_engine import build_vat_report
from app.services.odoo_balance_sheet_lines import build_odoo_balance_sheet_lines

REPORT_ALIASES: dict[str, str] = {
    "journals_audit": "journal_audit",
    "form_1099": "wht_report",
}

REPORT_CATALOG: list[dict[str, Any]] = [
    {"key": "profit_and_loss", "group": "statement", "name_en": "Profit and Loss", "name_sw": "Mapato na hasara"},
    {"key": "balance_sheet", "group": "statement", "name_en": "Balance Sheet", "name_sw": "Mizania"},
    {"key": "cash_flow", "group": "statement", "name_en": "Cash Flow Statement", "name_sw": "Mtiririko wa fedha"},
    {"key": "trial_balance", "group": "ledger", "name_en": "Trial Balance", "name_sw": "Mizani ya majaribio"},
    {"key": "general_ledger", "group": "ledger", "name_en": "General Ledger", "name_sw": "Leja kuu"},
    {"key": "partner_ledger", "group": "partner", "name_en": "Partner Ledger", "name_sw": "Leja ya washirika"},
    {"key": "aged_receivable", "group": "partner", "name_en": "Aged Receivable", "name_sw": "Madeni wateja"},
    {"key": "aged_payable", "group": "partner", "name_en": "Aged Payable", "name_sw": "Madeni wasambazaji"},
    {"key": "aged_partner_balance", "group": "partner", "name_en": "Aged Partner Balance", "name_sw": "Salio la washirika"},
    {"key": "open_items", "group": "partner", "name_en": "Open Items", "name_sw": "Vitu vilivyobaki"},
    {"key": "tax_report", "group": "tax", "name_en": "Tax Report", "name_sw": "Ripoti ya kodi"},
    {"key": "vat_report", "group": "tax", "name_en": "VAT Report (TRA)", "name_sw": "Ripoti ya VAT (TRA)"},
    {"key": "fiscal_report", "group": "tax", "name_en": "Fiscal Report (TRA/EFD)", "name_sw": "Ripoti ya TRA/EFD"},
    {"key": "wht_report", "group": "tax", "name_en": "Withholding Tax (WHT)", "name_sw": "Kodi ya zuio (WHT)"},
    {"key": "journal_report", "group": "audit", "name_en": "Journal Report", "name_sw": "Ripoti ya jarida"},
    {"key": "journal_audit", "group": "audit", "name_en": "Journals Audit", "name_sw": "Ukaguzi wa majarida"},
    {"key": "invoice_analysis", "group": "management", "name_en": "Invoice Analysis", "name_sw": "Uchambuzi wa ankara"},
    {"key": "account_analysis", "group": "management", "name_en": "Account Analysis", "name_sw": "Uchambuzi wa akaunti"},
    {"key": "analytic_report", "group": "management", "name_en": "Analytic Report", "name_sw": "Ripoti ya uchambuzi"},
    {"key": "executive_summary", "group": "management", "name_en": "Executive Summary", "name_sw": "Muhtasari wa uongozi"},
    {"key": "budget_report", "group": "management", "name_en": "Budget Report", "name_sw": "Ripoti ya bajeti"},
]


def _parse_dates(date_from: date | None, date_to: date | None) -> tuple[date, date]:
    today = date.today()
    d_to = date_to or today
    d_from = date_from or date(d_to.year, d_to.month, 1)
    if d_from > d_to:
        d_from, d_to = d_to, d_from
    return d_from, d_to


def _prior_period(d_from: date, d_to: date) -> tuple[date, date]:
    days = (d_to - d_from).days + 1
    prior_to = d_from - timedelta(days=1)
    prior_from = prior_to - timedelta(days=days - 1)
    return prior_from, prior_to


CASH_ACCOUNT_CODES = frozenset({"1000", "1100"})
AR_CONTROL_CODES = frozenset({"1200"})
AP_CONTROL_CODES = frozenset({"2000"})
VAT_PAYABLE_CODES = frozenset({"2100"})


def _net_to_debit_credit(net: float) -> tuple[float, float]:
    """Trial balance presentation: net = debit − credit → separate Dr/Cr columns."""
    if net >= 0:
        return round(net, 2), 0.0
    return 0.0, round(abs(net), 2)


def _row_flag(code: str, name: str, end_balance: float, account_type: str = "") -> str | None:
    low = name.lower()
    if "suspense" in low or code.startswith("999"):
        return "suspense"
    is_cash = code in CASH_ACCOUNT_CODES or ("cash" in low and account_type == "asset")
    if is_cash and end_balance < -0.01:
        return "negative_cash"
    if account_type == "asset" and code in CASH_ACCOUNT_CODES and end_balance < -0.01:
        return "negative_cash"
    return None


def _tb_balance_map(rows: list[dict]) -> dict[str, float]:
    return {str(r["code"]): float(r.get("end_balance") or 0) for r in rows}


def _build_reconciliation(
    *,
    bundle: dict,
    tb_rows: list[dict] | None,
    tb_totals: dict | None,
    vat_net: float | None,
) -> dict[str, Any]:
    bs = bundle.get("balance_sheet") or {}
    inc = bundle.get("income_statement") or {}
    ar_ops = round(sum(float(r.get("total") or 0) for r in bundle.get("aged_receivables") or []), 2)
    ap_ops = round(sum(float(r.get("total") or 0) for r in bundle.get("aged_payables") or []), 2)
    tb = _tb_balance_map(tb_rows or [])
    ar_ctrl = round(sum(tb.get(c, 0.0) for c in AR_CONTROL_CODES), 2)
    ap_ctrl = round(sum(abs(tb.get(c, 0.0)) for c in AP_CONTROL_CODES), 2)
    vat_ctrl = round(sum(tb.get(c, 0.0) for c in VAT_PAYABLE_CODES), 2)
    cash_tb = round(sum(tb.get(c, 0.0) for c in CASH_ACCOUNT_CODES), 2)
    cf = bundle.get("cash_flow") or {}
    td = float((tb_totals or {}).get("debit") or 0)
    tc = float((tb_totals or {}).get("credit") or 0)
    checks = [
        {
            "key": "tb_balanced",
            "label_en": "Trial balance: period debits equal credits",
            "label_sw": "Mizani ya majaribio: deni = mkopo (kipindi)",
            "ok": abs(td - tc) < 0.02,
            "detail": f"{td:.2f} vs {tc:.2f}",
        },
        {
            "key": "ar_aging",
            "label_en": "Aged receivables vs AR control (1200)",
            "label_sw": "Madeni wateja vs akaunti 1200",
            "ok": tb_rows is None or abs(ar_ops - ar_ctrl) < max(1.0, ar_ops * 0.05),
            "detail": f"aging {ar_ops:.2f} · control {ar_ctrl:.2f}",
        },
        {
            "key": "ap_aging",
            "label_en": "Aged payables vs AP control (2000)",
            "label_sw": "Madeni wasambazaji vs akaunti 2000",
            "ok": tb_rows is None or abs(ap_ops - ap_ctrl) < max(1.0, ap_ops * 0.05),
            "detail": f"aging {ap_ops:.2f} · control {ap_ctrl:.2f}",
        },
        {
            "key": "vat_tie",
            "label_en": "VAT report vs VAT payable (2100)",
            "label_sw": "Ripoti ya VAT vs 2100",
            "ok": vat_net is None or tb_rows is None or abs(vat_net - vat_ctrl) < max(1.0, abs(vat_net) * 0.1),
            "detail": f"vat {vat_net:.2f} · control {vat_ctrl:.2f}" if vat_net is not None else "",
        },
        {
            "key": "cash_flow_cash",
            "label_en": "Cash flow closing vs cash accounts (1000+1100)",
            "label_sw": "Mtiririko: fedha vs 1000+1100",
            "ok": tb_rows is None or abs(float(cf.get("cash_closing") or bs.get("cash") or 0) - cash_tb) < max(500.0, abs(cash_tb) * 0.15),
            "detail": f"ops {float(bs.get('cash') or 0):.2f} · tb {cash_tb:.2f}",
        },
        {
            "key": "pnl_retained",
            "label_en": "P&L net result recorded (operating view)",
            "label_sw": "Matokeo ya P&L yamehesabiwa",
            "ok": inc.get("net_before_tax") is not None,
            "detail": f"net {float(inc.get('net_before_tax') or 0):.2f}",
        },
    ]
    return {"checks": checks, "all_ok": all(c["ok"] for c in checks)}


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


async def _trial_initial_acc_rows(
    db: AsyncSession,
    tenant_id: str,
    branch_id: str | None,
    d_from: date,
    target_move: str,
) -> list[tuple[AccMoveLine, AccMove, LedgerAccount]]:
    fy_start = await fiscal_year_start(db, tenant_id, d_from)
    raw = await _acc_move_lines_query(
        db, tenant_id, branch_id, None, None, before_date=d_from, target_move=target_move
    )
    out: list[tuple[AccMoveLine, AccMove, LedgerAccount]] = []
    for line, move, acct in raw:
        if account_uses_fy_initial(acct.account_type) and move.date < fy_start:
            continue
        out.append((line, move, acct))
    return out


async def _trial_initial_journal_rows(
    db: AsyncSession,
    tenant_id: str,
    branch_id: str | None,
    d_from: date,
) -> list[tuple[JournalLine, JournalEntry, LedgerAccount]]:
    fy_start = await fiscal_year_start(db, tenant_id, d_from)
    raw = await _journal_lines_query(db, tenant_id, branch_id, None, None, before_date=d_from)
    out: list[tuple[JournalLine, JournalEntry, LedgerAccount]] = []
    for line, entry, acct in raw:
        if account_uses_fy_initial(acct.account_type) and entry.entry_date < fy_start:
            continue
        out.append((line, entry, acct))
    return out


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
    original_report_key = report_key
    report_key = REPORT_ALIASES.get(report_key, report_key)
    d_from, d_to = _parse_dates(date_from, date_to)
    fy_start = await fiscal_year_start(db, tenant_id, d_from)
    meta = {
        "report_key": report_key,
        "date_from": d_from.isoformat(),
        "date_to": d_to.isoformat(),
        "target_move": target_move,
        "currency": "TZS",
        "books_mode": books_mode,
        "fiscal_year_start": fy_start.isoformat(),
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
            p_from, p_to = _prior_period(d_from, d_to)
            prior = await build_report_bundle(
                db,
                tenant_id=tenant_id,
                branch_id=branch_id,
                books_mode=books_mode,
                date_from=p_from,
                date_to=p_to,
            )
            prior_inc = prior["income_statement"]
            comparatives = {
                "prior_date_from": p_from.isoformat(),
                "prior_date_to": p_to.isoformat(),
                "current": {
                    "revenue": inc.get("revenue"),
                    "gross_profit": inc.get("gross_profit"),
                    "net_before_tax": inc.get("net_before_tax"),
                },
                "prior": {
                    "revenue": prior_inc.get("revenue"),
                    "gross_profit": prior_inc.get("gross_profit"),
                    "net_before_tax": prior_inc.get("net_before_tax"),
                },
            }
            recon = _build_reconciliation(bundle=bundle, tb_rows=None, tb_totals=None, vat_net=None)
            if fin and _financial_lines_total(fin) > 0.01:
                return {
                    **meta,
                    "type": "hierarchy",
                    "data": {**inc, **fin},
                    "lines": fin.get("lines") or [],
                    "source": "ledger",
                    "comparatives": comparatives,
                    "reconciliation": recon,
                }
            return {
                **meta,
                "type": "hierarchy",
                "data": inc,
                "lines": inc.get("lines") or [],
                "source": "operations",
                "comparatives": comparatives,
                "reconciliation": recon,
            }
        if report_key == "balance_sheet":
            bs = bundle["balance_sheet"]
            inc = bundle["income_statement"]
            recon = _build_reconciliation(bundle=bundle, tb_rows=None, tb_totals=None, vat_net=None)
            tb_by: dict[str, float] = {}
            if fin and fin.get("lines"):
                for ln in fin.get("lines") or []:
                    if isinstance(ln, dict) and ln.get("account_code"):
                        tb_by[str(ln["account_code"])] = float(ln.get("amount") or 0)
            lines, totals = build_odoo_balance_sheet_lines(
                bs=bs,
                net_profit=float(inc.get("net_before_tax") or 0),
                tb_by_code=tb_by or None,
            )
            return {
                **meta,
                "type": "bs_odoo",
                "report_type": "bs",
                "period": d_to.isoformat(),
                "lines": lines,
                "totals": totals,
                "balanced": abs(totals["active"] - totals["passive"]) < 1.0,
                "source": "ledger" if fin and _financial_lines_total(fin) > 0.01 else "operations",
                "reconciliation": recon,
                "unposted_notice": True,
            }
        if report_key == "cash_flow":
            cf = bundle.get("cash_flow") or {}
            recon = _build_reconciliation(bundle=bundle, tb_rows=None, tb_totals=None, vat_net=None)
            return {**meta, "type": "cash_flow", "data": cf, "reconciliation": recon}
        if report_key == "aged_receivable":
            return {**meta, "type": "aged", "partner_type": "customer", "rows": bundle["aged_receivables"]}
        if report_key == "aged_payable":
            return {**meta, "type": "aged", "partner_type": "vendor", "rows": bundle["aged_payables"]}
        if report_key == "tax_report":
            inc = bundle["income_statement"]
            net = float(inc.get("net_before_tax") or 0)
            opex = float(inc.get("operating_expenses") or 0)
            payroll_est = round(opex * 0.45, 2)
            rows = [
                {
                    "section": "corporate",
                    "label_en": "Estimated taxable profit (operating)",
                    "label_sw": "Faida inayokadiriwa kodi",
                    "amount": net,
                },
                {
                    "section": "corporate",
                    "label_en": "Corporate tax (30% indicative)",
                    "label_sw": "Kodi ya makampuni (30%)",
                    "amount": round(max(0, net) * 0.30, 2),
                },
                {
                    "section": "payroll",
                    "label_en": "PAYE (payroll est. from opex)",
                    "label_sw": "PAYE (makadirio)",
                    "amount": round(payroll_est * 0.12, 2),
                },
                {
                    "section": "payroll",
                    "label_en": "SDL (3.5% on payroll est.)",
                    "label_sw": "SDL (3.5%)",
                    "amount": round(payroll_est * 0.035, 2),
                },
                {
                    "section": "payroll",
                    "label_en": "NSSF employer (10% est.)",
                    "label_sw": "NSSF mwajiri (10%)",
                    "amount": round(payroll_est * 0.10, 2),
                },
                {
                    "section": "payroll",
                    "label_en": "WCF (0.5% on payroll est.)",
                    "label_sw": "WCF (0.5%)",
                    "amount": round(payroll_est * 0.005, 2),
                },
                {
                    "section": "vat",
                    "label_en": "Output VAT (sales / EFD)",
                    "label_sw": "VAT mauzo (EFD)",
                    "amount": inc.get("vat_output", 0),
                },
                {
                    "section": "vat",
                    "label_en": "Net operating (excl. VAT)",
                    "label_sw": "Uendeshaji (bila VAT)",
                    "amount": net,
                },
            ]
            return {
                **meta,
                "type": "tax",
                "rows": rows,
                "note_en": "Indicative TRA lines — confirm with your accountant and TRA returns.",
                "note_sw": "Makadirio ya TRA — thibitisha na mhasibu na TRA.",
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
            initial_rows = await _trial_initial_acc_rows(db, tenant_id, branch_id, d_from, target_move)
            period_rows = await _acc_move_lines_query(
                db, tenant_id, branch_id, d_from, d_to, target_move=target_move
            )
        else:
            initial_rows = await _trial_initial_journal_rows(db, tenant_id, branch_id, d_from)
            period_rows = await _journal_lines_query(db, tenant_id, branch_id, d_from, d_to)
        totals: dict[str, dict] = {}

        def add(rows, field: str):
            for line, _entry, acct in rows:
                b = totals.setdefault(
                    acct.code,
                    {
                        "code": acct.code,
                        "name": acct.name,
                        "account_type": acct.account_type,
                        "initial_balance": 0.0,
                        "debit": 0.0,
                        "credit": 0.0,
                    },
                )
                if field == "initial":
                    b["initial_balance"] += float(line.debit or 0) - float(line.credit or 0)
                else:
                    b["debit"] += float(line.debit or 0)
                    b["credit"] += float(line.credit or 0)

        add(initial_rows, "initial")
        add(period_rows, "period")
        out = []
        flags: list[dict[str, Any]] = []
        sum_open_dr = sum_open_cr = sum_close_dr = sum_close_cr = 0.0
        for code in sorted(totals.keys()):
            t = totals[code]
            opening = round(t["initial_balance"], 2)
            end = round(opening + t["debit"] - t["credit"], 2)
            open_dr, open_cr = _net_to_debit_credit(opening)
            close_dr, close_cr = _net_to_debit_credit(end)
            sum_open_dr += open_dr
            sum_open_cr += open_cr
            sum_close_dr += close_dr
            sum_close_cr += close_cr
            flag = _row_flag(code, t["name"], end, str(t.get("account_type") or ""))
            if flag:
                flags.append({"code": code, "name": t["name"], "flag": flag, "end_balance": end})
            out.append(
                {
                    **t,
                    "initial_balance": opening,
                    "opening_debit": open_dr,
                    "opening_credit": open_cr,
                    "debit": round(t["debit"], 2),
                    "credit": round(t["credit"], 2),
                    "end_balance": end,
                    "closing_debit": close_dr,
                    "closing_credit": close_cr,
                    "flag": flag,
                }
            )
        td = round(sum(r["debit"] for r in out), 2)
        tc = round(sum(r["credit"] for r in out), 2)
        if abs(td - tc) >= 0.02:
            flags.append({"code": "", "name": "Period totals", "flag": "unbalanced_tb", "end_balance": td - tc})
        negative_cash = [r for r in out if r.get("flag") == "negative_cash"]
        suggestions: list[dict[str, Any]] = []
        if negative_cash:
            need = round(sum(abs(float(r["end_balance"])) for r in negative_cash), 2)
            suggestions.append(
                {
                    "key": "opening_cash_equity",
                    "label_en": f"Post opening entry: Dr Cash {need:,.0f} / Cr Owner's equity (3000)",
                    "label_sw": f"Ingizo la mwanzo: Dr Fedha {need:,.0f} / Cr Mtaji (3000)",
                    "lines": [
                        {"account_code": "1000", "debit": need, "credit": 0, "label": "Opening cash correction"},
                        {"account_code": "3000", "debit": 0, "credit": need, "label": "Owner capital / opening balance"},
                    ],
                }
            )
        bundle = await build_report_bundle(
            db,
            tenant_id=tenant_id,
            branch_id=branch_id,
            books_mode=books_mode,
            date_from=d_from,
            date_to=d_to,
        )
        vat_net = None
        try:
            vat_net = float((await build_vat_report(db, tenant_id=tenant_id, date_from=d_from, date_to=d_to)).get("net_vat") or 0)
        except Exception:
            vat_net = None
        recon = _build_reconciliation(bundle=bundle, tb_rows=out, tb_totals={"debit": td, "credit": tc}, vat_net=vat_net)
        return {
            **meta,
            "type": "trial_balance",
            "rows": out,
            "totals": {
                "period_debit": td,
                "period_credit": tc,
                "opening_debit": round(sum_open_dr, 2),
                "opening_credit": round(sum_open_cr, 2),
                "closing_debit": round(sum_close_dr, 2),
                "closing_credit": round(sum_close_cr, 2),
            },
            "totals_row": {
                "code": "",
                "name": "Total",
                "opening_debit": round(sum_open_dr, 2),
                "opening_credit": round(sum_open_cr, 2),
                "debit": td,
                "credit": tc,
                "closing_debit": round(sum_close_dr, 2),
                "closing_credit": round(sum_close_cr, 2),
            },
            "flags": flags,
            "suggestions": suggestions,
            "reconciliation": recon,
        }

    if report_key == "general_ledger":
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
        opening: dict[str, float] = {}
        for line, _entry, acct in initial_rows:
            opening[acct.code] = opening.get(acct.code, 0.0) + float(line.debit or 0) - float(line.credit or 0)
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
            move_id = getattr(entry, "id", "")
            posted_by = getattr(entry, "posted_by", None) or ""
            g["lines"].append(
                {
                    "date": ref_date,
                    "reference": reference,
                    "label": label,
                    "debit": float(line.debit or 0),
                    "credit": float(line.credit or 0),
                    "source": source,
                    "partner_name": getattr(line, "partner_name", "") or "",
                    "move_id": move_id,
                    "posted_by": posted_by,
                }
            )
        accounts = [by_acct[k] for k in sorted(by_acct.keys())]
        for a in accounts:
            a["debit"] = round(a["debit"], 2)
            a["credit"] = round(a["credit"], 2)
            a["opening_balance"] = round(opening.get(a["code"], 0.0), 2)
            running = a["opening_balance"]
            a["lines"].sort(key=lambda x: (x["date"], x["reference"]))
            for ln in a["lines"]:
                running = round(running + float(ln["debit"]) - float(ln["credit"]), 2)
                ln["running_balance"] = running
            a["balance"] = running
        td = round(sum(float(a["debit"]) for a in accounts), 2)
        tc = round(sum(float(a["credit"]) for a in accounts), 2)
        return {
            **meta,
            "type": "general_ledger",
            "accounts": accounts,
            "totals": {"debit": td, "credit": tc, "balanced": abs(td - tc) < 0.02},
        }

    if report_key in ("journal_report", "journal_audit"):
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
        for e in journals:
            td = round(sum(float(ln["debit"]) for ln in e["lines"]), 2)
            tc = round(sum(float(ln["credit"]) for ln in e["lines"]), 2)
            src = str(e.get("source") or "")
            flags: list[str] = []
            if src in ("", "manual"):
                flags.append("manual")
            if abs(td - tc) >= 0.02:
                flags.append("unbalanced")
            e["total_debit"] = td
            e["total_credit"] = tc
            e["flags"] = flags
        seq = [f"{j['date']} · {j['reference']}" for j in journals]
        jtype = "journal_audit" if report_key == "journal_audit" else "journal_report"
        return {**meta, "type": jtype, "report_key": report_key, "entries": journals, "sequence": seq}

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
        period_total = 0.0
        bundle_hint = await build_report_bundle(
            db, tenant_id=tenant_id, branch_id=branch_id, books_mode=books_mode, date_from=d_from, date_to=d_to
        )
        cost_hint = bundle_hint.get("income_statement") or {}
        for s in sales:
            if s.created_at and s.created_at.date() < d_from:
                continue
            if s.created_at and s.created_at.date() > d_to:
                continue
            stotal = float(s.total or 0)
            period_total += stotal
            cname = (s.customer_name or "Walk-in").strip()
            by_customer.setdefault(cname, {"name": cname, "count": 0, "total": 0.0})
            by_customer[cname]["count"] += 1
            by_customer[cname]["total"] += stotal
            for raw in s.items or []:
                item = raw if isinstance(raw, dict) else {}
                pname = str(item.get("product_name") or "Item")
                by_product.setdefault(pname, {"name": pname, "qty": 0.0, "total": 0.0})
                by_product[pname]["qty"] += float(item.get("quantity") or 0)
                by_product[pname]["total"] += float(item.get("total") or item.get("totalPrice") or 0)
        rev = float(cost_hint.get("revenue") or period_total or 1)
        cogs = float(cost_hint.get("cogs") or 0)
        gross_margin = round((rev - cogs) / rev * 100, 1) if rev else 0.0
        return {
            **meta,
            "type": "invoice_analysis",
            "by_customer": sorted(by_customer.values(), key=lambda x: x["total"], reverse=True)[:100],
            "by_product": sorted(by_product.values(), key=lambda x: x["total"], reverse=True)[:100],
            "ratios": {
                "revenue": round(rev, 2),
                "gross_margin_pct": gross_margin,
                "invoice_count": sum(c["count"] for c in by_customer.values()),
                "avg_invoice": round(period_total / max(1, sum(c["count"] for c in by_customer.values())), 2),
            },
        }

    if report_key == "account_analysis":
        accounts = await ensure_default_chart(db, tenant_id)
        bundle = await build_report_bundle(
            db,
            tenant_id=tenant_id,
            branch_id=branch_id,
            books_mode=books_mode,
            date_from=d_from,
            date_to=d_to,
        )
        bs = bundle.get("balance_sheet") or {}
        inc = bundle.get("income_statement") or {}
        assets = float(bs.get("total_assets") or 0)
        liab = float(bs.get("total_liabilities") or 0)
        recv = float(bs.get("receivables") or 0)
        pay = float(bs.get("payables") or 0)
        rev = float(inc.get("revenue") or 0)
        cogs = float(inc.get("cogs") or 0)
        inv_val = float(bs.get("inventory") or 0)
        current_ratio = round(assets / liab, 2) if liab > 0 else None
        dso = round(recv / rev * 30, 1) if rev > 0 else None
        dpo = round(pay / cogs * 30, 1) if cogs > 0 else None
        stock_days = round(inv_val / cogs * 30, 1) if cogs > 0 else None
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
                "ratios": {
                    "current_ratio": current_ratio,
                    "gross_margin_pct": round((rev - cogs) / rev * 100, 1) if rev else None,
                    "dso_days": dso,
                    "dpo_days": dpo,
                    "inventory_days": stock_days,
                },
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
            "ratios": {
                "current_ratio": current_ratio,
                "gross_margin_pct": round((rev - cogs) / rev * 100, 1) if rev else None,
                "dso_days": dso,
                "dpo_days": dpo,
                "inventory_days": stock_days,
            },
        }

    if report_key == "fiscal_report":
        bundle = await build_report_bundle(
            db,
            tenant_id=tenant_id,
            branch_id=branch_id,
            books_mode="tra",
            date_from=d_from,
            date_to=d_to,
        )
        inc = bundle["income_statement"]
        hq_id = await get_tenant_default_branch_id(db, tenant_id) if branch_id else None
        q = select(Sale).where(
            Sale.tenant_id == tenant_id,
            Sale.status.in_(tuple(COMPLETED_SALE_STATUSES)),
            Sale.tra_efd_signature != None,  # noqa: E711
        )
        clause = branch_id_filter(Sale.branch_id, branch_id, hq_id)
        if clause is not None:
            q = q.where(clause)
        fiscal_sales = 0
        for s in (await db.execute(q)).scalars().all():
            if s.created_at and (s.created_at.date() < d_from or s.created_at.date() > d_to):
                continue
            fiscal_sales += 1
        rows = [
            {"label_en": "Fiscal receipts (EFD) count", "label_sw": "Idadi ya risiti za EFD", "amount": fiscal_sales},
            {"label_en": "Gross sales (period)", "label_sw": "Mauzo jumla", "amount": inc.get("revenue", 0)},
            {"label_en": "Output VAT (EFD)", "label_sw": "VAT mauzo", "amount": inc.get("vat_output", 0)},
            {"label_en": "Net sales excl. VAT", "label_sw": "Mauzo bila VAT", "amount": inc.get("net_before_tax", 0)},
        ]
        return {
            **meta,
            "type": "fiscal",
            "rows": rows,
            "note_en": "Cross-check with TRA VFD/Z reports before filing.",
            "note_sw": "Linganisha na ripoti za TRA kabla ya kuwasilisha.",
        }

    if report_key == "aged_partner_balance":
        bundle = await build_report_bundle(
            db,
            tenant_id=tenant_id,
            branch_id=branch_id,
            books_mode=books_mode,
            date_from=d_from,
            date_to=d_to,
        )
        rec = bundle["aged_receivables"]
        pay = bundle["aged_payables"]
        return {
            **meta,
            "type": "aged_partner_balance",
            "receivables": rec,
            "payables": pay,
            "totals": {
                "receivable": round(sum(float(r.get("total") or 0) for r in rec), 2),
                "payable": round(sum(float(r.get("total") or 0) for r in pay), 2),
            },
        }

    if report_key == "wht_report":
        bundle = await build_report_bundle(
            db, tenant_id=tenant_id, branch_id=branch_id, books_mode=books_mode, date_from=d_from, date_to=d_to
        )
        payables = float(bundle.get("balance_sheet", {}).get("payables") or 0)
        wht_est = round(payables * 0.02, 2)
        return {
            **meta,
            "type": "tax",
            "rows": [
                {"label_en": "Supplier payments base (est.)", "label_sw": "Msingi wa malipo wasambazaji", "amount": payables},
                {"label_en": "WHT 2% (indicative)", "label_sw": "WHT 2% (makadirio)", "amount": wht_est},
            ],
            "note_en": "TZ WHT varies by payment type — confirm with TRA and contracts.",
            "note_sw": "WHT inabadilika — thibitisha na TRA.",
        }

    if report_key == "executive_summary":
        bundle = await build_report_bundle(
            db, tenant_id=tenant_id, branch_id=branch_id, books_mode=books_mode, date_from=d_from, date_to=d_to
        )
        inc = bundle["income_statement"]
        bs = bundle["balance_sheet"]
        stats = bundle.get("stats") or {}
        return {
            **meta,
            "type": "executive_summary",
            "kpis": [
                {"label_en": "Revenue", "label_sw": "Mapato", "amount": inc.get("revenue", 0)},
                {"label_en": "Gross profit", "label_sw": "Faida jumla", "amount": inc.get("gross_profit", 0)},
                {"label_en": "Net result", "label_sw": "Matokeo", "amount": inc.get("net_before_tax", 0)},
                {"label_en": "Cash (est.)", "label_sw": "Fedha", "amount": bs.get("cash", 0)},
                {"label_en": "Inventory", "label_sw": "Stoo", "amount": bs.get("inventory", 0)},
                {"label_en": "Completed sales", "label_sw": "Mauzo", "amount": stats.get("completed_sales_count", 0)},
            ],
            "weekly": bundle.get("weekly_activity") or [],
        }

    if report_key == "budget_report":
        bundle = await build_report_bundle(
            db, tenant_id=tenant_id, branch_id=branch_id, books_mode=books_mode, date_from=d_from, date_to=d_to
        )
        inc = bundle["income_statement"]
        actual_opex = float(inc.get("operating_expenses") or 0)
        budget_opex = round(actual_opex * 1.08, 2) if actual_opex else 0
        actual_rev = float(inc.get("revenue") or 0)
        budget_rev = round(actual_rev * 1.05, 2) if actual_rev else 0
        return {
            **meta,
            "type": "budget_report",
            "rows": [
                {
                    "label_en": "Revenue",
                    "label_sw": "Mapato",
                    "actual": actual_rev,
                    "budget": budget_rev,
                    "variance": round(actual_rev - budget_rev, 2),
                },
                {
                    "label_en": "Operating expenses",
                    "label_sw": "Matumizi",
                    "actual": actual_opex,
                    "budget": budget_opex,
                    "variance": round(budget_opex - actual_opex, 2),
                },
            ],
            "note_en": "Budget lines are indicative until you configure targets in Settings.",
            "note_sw": "Bajeti ni makadirio hadi usanidi lengo.",
        }

    if report_key == "analytic_report":
        bundle = await build_report_bundle(
            db, tenant_id=tenant_id, branch_id=branch_id, books_mode=books_mode, date_from=d_from, date_to=d_to
        )
        hq_id = await get_tenant_default_branch_id(db, tenant_id) if branch_id else None
        q = select(Sale).where(Sale.tenant_id == tenant_id, Sale.status.in_(tuple(COMPLETED_SALE_STATUSES)))
        clause = branch_id_filter(Sale.branch_id, branch_id, hq_id)
        if clause is not None:
            q = q.where(clause)
        by_type: dict[str, float] = {}
        for s in (await db.execute(q)).scalars().all():
            if s.created_at and (s.created_at.date() < d_from or s.created_at.date() > d_to):
                continue
            key = s.sale_type or "retail"
            by_type[key] = by_type.get(key, 0.0) + float(s.total or 0)
        return {
            **meta,
            "type": "analytic_report",
            "by_sale_type": [{"name": k, "total": round(v, 2)} for k, v in sorted(by_type.items(), key=lambda x: -x[1])],
            "income": bundle.get("income_statement"),
        }

    return {**meta, "type": "unknown", "error": f"Unknown report: {original_report_key}"}
