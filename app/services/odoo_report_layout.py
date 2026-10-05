"""Odoo / Zalongwa-style report HTML layout (PDF via browser print)."""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any

ODOO_REPORT_CSS = """
body { font-family: Helvetica, Arial, "Segoe UI", sans-serif; font-size: 11px; color: #000; margin: 0; padding: 16px 20px; }
.o_report_page { max-width: 960px; margin: 0 auto; }
.o_company { margin-bottom: 8px; }
.o_company_name { font-size: 14px; font-weight: bold; }
.o_company_meta { font-size: 10px; color: #444; line-height: 1.4; }
.o_report_title { font-size: 18px; font-weight: bold; margin: 12px 0 8px; color: #000; }
.o_filters { display: table; width: 100%; margin: 12px 0 16px; border-collapse: collapse; }
.o_filters_row { display: table-row; }
.o_filters_cell { display: table-cell; width: 33%; vertical-align: top; padding: 4px 8px 4px 0; font-size: 10px; }
.o_filters_cell strong { display: block; font-size: 10px; margin-bottom: 2px; }
.table-reports, .act_as_table { width: 100%; border-collapse: collapse; font-size: 10px; }
.table-reports th, .act_as_cell.head { background: #f0f0f0; border: 1px solid #ddd; padding: 4px 6px; text-align: left; font-weight: bold; }
.table-reports td, .act_as_cell { border: 1px solid #e8e8e8; padding: 3px 6px; vertical-align: top; }
.text-end, .num { text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }
tr.labels td, tr.section td { background: #f0f0f0; font-weight: bold; }
tr.section_total td { font-weight: bold; border-top: 2px solid #666; background: #f8f8f8; }
tr.grand_total td { font-weight: bold; background: #e8e8e8; }
tr.total td { font-weight: bold; border-top: 1px solid #999; }
.indent-0 { padding-left: 6px; }
.indent-1 { padding-left: 18px; }
.indent-2 { padding-left: 30px; }
.indent-3 { padding-left: 42px; }
.indent-4 { padding-left: 54px; }
.indent-5 { padding-left: 66px; }
.o_footer { margin-top: 24px; padding-top: 8px; border-top: 1px solid #ddd; font-size: 9px; color: #666; display: flex; justify-content: space-between; }
@media print { body { padding: 8mm; } @page { margin: 12mm; } }
"""


def _esc(s: Any) -> str:
    return html.escape(str(s or ""))


def _money(n: Any) -> str:
    try:
        v = float(n or 0)
    except (TypeError, ValueError):
        v = 0.0
    return f"{v:,.2f}"


def _indent_class(level: int) -> str:
    return f"indent-{min(max(level, 0), 5)}"


def filters_html(
    *,
    target_move: str = "posted",
    date_from: str | None = None,
    date_to: str | None = None,
    books_mode: str | None = None,
    branch_id: str | None = None,
    extra: str | None = None,
) -> str:
    tm = "All Posted Entries" if target_move == "posted" else "All Entries"
    period = ""
    if date_from:
        period += f"<strong>Date from:</strong> {_esc(date_from)}<br/>"
    if date_to:
        period += f"<strong>Date to:</strong> {_esc(date_to)}"
    books = f"<strong>Books:</strong> {_esc(books_mode or 'standard')}"
    branch = f"<strong>Branch:</strong> {_esc(branch_id or 'All')}"
    return f"""
<div class="o_filters">
  <div class="o_filters_row">
    <div class="o_filters_cell"><strong>Target Moves:</strong><p>{tm}</p></div>
    <div class="o_filters_cell">{period}</div>
    <div class="o_filters_cell">{books}<br/>{branch}</div>
  </div>
</div>
{extra or ""}
"""


def hierarchy_lines_table(lines: list[dict[str, Any]], *, amount_header: str = "Balance") -> str:
    rows = [
        f"<table class='table-reports'><thead><tr><th>Name</th><th class='text-end'>{_esc(amount_header)}</th></tr></thead><tbody>"
    ]
    for ln in lines:
        level = int(ln.get("level") or 0)
        style = str(ln.get("style") or "line")
        cls = "section" if style in ("header", "section_total", "section") else ""
        if style == "section_total":
            cls = "section_total"
        elif style in ("total", "grand_total"):
            cls = style
        bold = "" if level > 3 and style == "line" else ("font-weight:bold;" if style == "header" else "")
        amt = _money(ln.get("amount"))
        rows.append(
            f"<tr class='{cls}'><td class='{_indent_class(level)}' style='{bold}'>{_esc(ln.get('name'))}</td>"
            f"<td class='num' style='{bold}'>{amt}</td></tr>"
        )
    rows.append("</tbody></table>")
    return "\n".join(rows)


def trial_balance_table(payload: dict[str, Any]) -> str:
    rows = payload.get("rows") or []
    lines = [
        "<table class='table-reports'><thead><tr>"
        "<th>Code</th><th>Account</th>"
        "<th class='text-end'>Opening Dr</th><th class='text-end'>Opening Cr</th>"
        "<th class='text-end'>Debit</th><th class='text-end'>Credit</th>"
        "<th class='text-end'>Closing Dr</th><th class='text-end'>Closing Cr</th>"
        "</tr></thead><tbody>"
    ]
    for r in rows:
        lines.append(
            f"<tr><td>{_esc(r.get('code'))}</td><td>{_esc(r.get('name'))}</td>"
            f"<td class='num'>{_money(r.get('opening_debit'))}</td>"
            f"<td class='num'>{_money(r.get('opening_credit'))}</td>"
            f"<td class='num'>{_money(r.get('debit'))}</td>"
            f"<td class='num'>{_money(r.get('credit'))}</td>"
            f"<td class='num'>{_money(r.get('closing_debit'))}</td>"
            f"<td class='num'>{_money(r.get('closing_credit'))}</td></tr>"
        )
    tr = payload.get("totals_row") or {}
    if tr:
        lines.append(
            f"<tr class='total'><td colspan='2'>Total</td>"
            f"<td class='num'>{_money(tr.get('opening_debit'))}</td>"
            f"<td class='num'>{_money(tr.get('opening_credit'))}</td>"
            f"<td class='num'>{_money(tr.get('debit'))}</td>"
            f"<td class='num'>{_money(tr.get('credit'))}</td>"
            f"<td class='num'>{_money(tr.get('closing_debit'))}</td>"
            f"<td class='num'>{_money(tr.get('closing_credit'))}</td></tr>"
        )
    lines.append("</tbody></table>")
    td = float(tr.get("debit") or 0) if tr else 0
    tc = float(tr.get("credit") or 0) if tr else 0
    if not tr and rows:
        td = round(sum(float(r.get("debit") or 0) for r in rows), 2)
        tc = round(sum(float(r.get("credit") or 0) for r in rows), 2)
    if rows:
        ok = abs(td - tc) < 0.02
        status = "Period totals balanced ✓" if ok else f"Period out of balance by {_money(abs(td - tc))}"
        lines.append(
            f"<p class='total' style='margin-top:8px;font-weight:bold'>Total debit {_money(td)} · "
            f"Total credit {_money(tc)} · {status}</p>"
        )
    return "\n".join(lines)


def general_ledger_table(accounts: list[dict[str, Any]], totals: dict[str, Any] | None = None) -> str:
    parts: list[str] = []
    td = float((totals or {}).get("debit") or 0)
    tc = float((totals or {}).get("credit") or 0)
    if not td and not tc and accounts:
        td = round(sum(float(a.get("debit") or 0) for a in accounts), 2)
        tc = round(sum(float(a.get("credit") or 0) for a in accounts), 2)
    for acct in accounts:
        parts.append(f"<p style='font-weight:bold;margin:12px 0 4px'>{_esc(acct.get('code'))} {_esc(acct.get('name'))}</p>")
        parts.append(
            "<table class='table-reports'><thead><tr>"
            "<th>Date</th><th>Reference</th><th>Label</th>"
            "<th class='text-end'>Debit</th><th class='text-end'>Credit</th><th class='text-end'>Balance</th>"
            "</tr></thead><tbody>"
        )
        for ln in acct.get("lines") or []:
            bal = ln.get("running_balance")
            if bal is None:
                bal = ln.get("balance")
            parts.append(
                f"<tr><td>{_esc(ln.get('date'))}</td><td>{_esc(ln.get('reference'))}</td><td>{_esc(ln.get('label'))}</td>"
                f"<td class='num'>{_money(ln.get('debit'))}</td>"
                f"<td class='num'>{_money(ln.get('credit'))}</td>"
                f"<td class='num'>{_money(bal)}</td></tr>"
            )
        parts.append("</tbody></table>")
    if accounts:
        ok = abs(td - tc) < 0.02
        status = "Balanced ✓" if ok else f"Out of balance by {_money(abs(td - tc))}"
        parts.append(
            f"<p class='total' style='margin-top:12px;font-weight:bold'>Total General Ledger: "
            f"Debit {_money(td)} · Credit {_money(tc)} · {status}</p>"
        )
    return "\n".join(parts) if parts else "<p>No ledger lines.</p>"


def aged_table(rows: list[dict[str, Any]]) -> str:
    lines = [
        "<table class='table-reports'><thead><tr>"
        "<th>Partner</th><th class='text-end'>Not due</th><th class='text-end'>1-30</th>"
        "<th class='text-end'>31-60</th><th class='text-end'>61-90</th><th class='text-end'>90+</th>"
        "<th class='text-end'>Total</th></tr></thead><tbody>"
    ]
    for r in rows:
        lines.append(
            f"<tr><td>{_esc(r.get('name'))}</td>"
            f"<td class='num'>{_money(r.get('current'))}</td>"
            f"<td class='num'>{_money(r.get('d30'))}</td>"
            f"<td class='num'>{_money(r.get('d60'))}</td>"
            f"<td class='num'>{_money(r.get('d90'))}</td>"
            f"<td class='num'>{_money(r.get('d90plus'))}</td>"
            f"<td class='num'>{_money(r.get('total'))}</td></tr>"
        )
    lines.append("</tbody></table>")
    return "\n".join(lines)


def label_amount_rows(rows: list[dict[str, Any]], *, label_key: str = "label_en") -> str:
    lines = [
        f"<table class='table-reports'><thead><tr><th>Description</th><th class='text-end'>Balance</th></tr></thead><tbody>"
    ]
    for i, r in enumerate(rows):
        label = r.get(label_key) or r.get("label_sw") or r.get("label") or ""
        cls = "total" if i == len(rows) - 1 and len(rows) > 1 else ""
        lines.append(
            f"<tr class='{cls}'><td>{_esc(label)}</td><td class='num'>{_money(r.get('amount'))}</td></tr>"
        )
    lines.append("</tbody></table>")
    return "\n".join(lines)


def open_items_table(rows: list[dict[str, Any]]) -> str:
    lines = [
        "<table class='table-reports'><thead><tr>"
        "<th>Date</th><th>Move</th><th>Partner</th><th>Label</th><th>Account</th><th class='text-end'>Open</th>"
        "</tr></thead><tbody>"
    ]
    for r in rows:
        lines.append(
            f"<tr><td>{_esc(r.get('date'))}</td><td>{_esc(r.get('move_name'))}</td>"
            f"<td>{_esc(r.get('partner_name'))}</td><td>{_esc(r.get('label'))}</td>"
            f"<td>{_esc(r.get('account_code'))}</td><td class='num'>{_money(r.get('amount'))}</td></tr>"
        )
    lines.append("</tbody></table>")
    return "\n".join(lines)


def journal_entries_table(entries: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for e in entries:
        parts.append(
            f"<p style='font-weight:bold;margin:10px 0 2px'>{_esc(e.get('date'))} · {_esc(e.get('reference'))}</p>"
        )
        parts.append(
            "<table class='table-reports'><thead><tr>"
            "<th>Account</th><th>Label</th><th class='text-end'>Debit</th><th class='text-end'>Credit</th>"
            "</tr></thead><tbody>"
        )
        for ln in e.get("lines") or []:
            parts.append(
                f"<tr><td>{_esc(ln.get('account_code'))} {_esc(ln.get('account_name'))}</td>"
                f"<td>{_esc(ln.get('label'))}</td>"
                f"<td class='num'>{_money(ln.get('debit'))}</td>"
                f"<td class='num'>{_money(ln.get('credit'))}</td></tr>"
            )
        parts.append("</tbody></table>")
    return "\n".join(parts) if parts else "<p>No journal entries.</p>"


def partner_ledger_table(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    for section, key in (("Customers", "customers"), ("Vendors", "vendors")):
        rows = payload.get(key) or []
        if not rows:
            continue
        parts.append(f"<h3 style='font-size:12px;margin:10px 0 4px'>{section}</h3>")
        parts.append(
            "<table class='table-reports'><thead><tr><th>Partner</th><th class='text-end'>Balance</th></tr></thead><tbody>"
        )
        for r in rows:
            parts.append(
                f"<tr><td>{_esc(r.get('name'))}</td><td class='num'>{_money(r.get('total'))}</td></tr>"
            )
        parts.append("</tbody></table>")
    return "\n".join(parts) if parts else "<p>No partner balances.</p>"


def kpi_table(kpis: list[dict[str, Any]]) -> str:
    return label_amount_rows(kpis, label_key="label_en")


def payload_to_odoo_body_html(payload: dict[str, Any]) -> str:
    rtype = str(payload.get("type") or "")
    if rtype == "trial_balance":
        return trial_balance_table(payload)
    if rtype in ("bs_odoo", "pnl_odoo", "hierarchy", "budget_report"):
        lines = payload.get("lines") or (payload.get("data") or {}).get("lines") or []
        if rtype == "budget_report":
            rows = payload.get("rows") or []
            if rows:
                parts = ["<table class='table-reports'><thead><tr><th>Line</th><th class='text-end'>Actual</th>"
                         "<th class='text-end'>Budget</th><th class='text-end'>Variance</th></tr></thead><tbody>"]
                for r in rows:
                    parts.append(
                        f"<tr><td>{_esc(r.get('label_en'))}</td>"
                        f"<td class='num'>{_money(r.get('actual'))}</td>"
                        f"<td class='num'>{_money(r.get('budget'))}</td>"
                        f"<td class='num'>{_money(r.get('variance'))}</td></tr>"
                    )
                parts.append("</tbody></table>")
                return "\n".join(parts)
        hdr = "Balance" if rtype == "bs_odoo" else "Amount"
        body = hierarchy_lines_table(list(lines), amount_header=hdr)
        if rtype == "bs_odoo":
            totals = payload.get("totals") or {}
            active = float(totals.get("active") or 0)
            passive = float(totals.get("passive") or 0)
            if not active and not passive and lines:
                for ln in lines:
                    if str(ln.get("style")) == "section_total":
                        if str(ln.get("side")) == "active":
                            active = float(ln.get("amount") or 0)
                        if str(ln.get("side")) == "passive":
                            passive = float(ln.get("amount") or 0)
            ok = abs(active - passive) < 1.0
            note = (
                f"Balanced: Assets {_money(active)} = Liabilities + Equity {_money(passive)}"
                if ok
                else f"Out of balance — difference {_money(abs(active - passive))}"
            )
            body += f"<p class='total' style='margin-top:12px;font-weight:bold'>{note}</p>"
        return body
    if rtype == "balance_sheet":
        data = payload.get("data") or {}
        fake_lines = [
            {"name": "Cash & bank", "level": 1, "amount": data.get("cash"), "style": "line"},
            {"name": "Receivables", "level": 1, "amount": data.get("receivables"), "style": "line"},
            {"name": "Inventory", "level": 1, "amount": data.get("inventory"), "style": "line"},
            {"name": "Total assets", "level": 0, "amount": data.get("total_assets"), "style": "section_total"},
            {"name": "Payables", "level": 1, "amount": data.get("payables"), "style": "line"},
            {"name": "VAT payable", "level": 1, "amount": data.get("vat_payable"), "style": "line"},
            {"name": "Equity", "level": 1, "amount": data.get("equity_estimate"), "style": "line"},
        ]
        return hierarchy_lines_table(fake_lines)
    if rtype == "general_ledger":
        return general_ledger_table(payload.get("accounts") or [], payload.get("totals"))
    if rtype == "aged":
        return aged_table(payload.get("rows") or [])
    if rtype == "aged_partner_balance":
        rec = payload.get("receivables") or []
        pay = payload.get("payables") or []
        return aged_table(rec) + "<br/>" + aged_table(pay)
    if rtype in ("tax", "vat", "fiscal"):
        return label_amount_rows(payload.get("rows") or [])
    if rtype == "open_items":
        return open_items_table(payload.get("rows") or [])
    if rtype in ("journal_report", "journal_audit"):
        return journal_entries_table(payload.get("entries") or [])
    if rtype == "partner_ledger":
        return partner_ledger_table(payload)
    if rtype == "executive_summary":
        return kpi_table(payload.get("kpis") or [])
    if rtype == "cash_flow":
        data = payload.get("data") or {}
        rows = [
            {"label_en": "Cash opening", "amount": data.get("cash_opening")},
            {"label_en": "Operating", "amount": data.get("operating")},
            {"label_en": "Investing", "amount": data.get("investing")},
            {"label_en": "Financing", "amount": data.get("financing")},
            {"label_en": "Cash closing", "amount": data.get("cash_closing")},
        ]
        return label_amount_rows(rows)
    if rtype == "account_analysis":
        rows = [{"label_en": x.get("type"), "amount": x.get("net")} for x in (payload.get("by_account_type") or [])]
        return label_amount_rows(rows)
    if rtype == "analytic_report":
        return label_amount_rows(
            [{"label_en": x.get("name"), "amount": x.get("total")} for x in (payload.get("by_sale_type") or [])]
        )
    if rtype == "invoice_analysis":
        by_c = payload.get("by_customer") or []
        lines = [
            "<table class='table-reports'><thead><tr><th>Customer</th><th class='text-end'>Total</th></tr></thead><tbody>"
        ]
        for r in by_c:
            lines.append(f"<tr><td>{_esc(r.get('name'))}</td><td class='num'>{_money(r.get('total'))}</td></tr>")
        lines.append("</tbody></table>")
        return "\n".join(lines)
    return f"<p>No printable layout for report type: {_esc(rtype)}</p>"


def render_odoo_report_html(
    *,
    business_name: str,
    report_title: str,
    meta: dict[str, Any],
    body_html: str,
    tax_id: str | None = None,
    address_lines: list[str] | None = None,
) -> str:
    addr = "<br/>".join(_esc(x) for x in (address_lines or []) if x)
    tax = f"<br/>Tax ID: {_esc(tax_id)}" if tax_id else ""
    filt = filters_html(
        target_move=str(meta.get("target_move") or "posted"),
        date_from=meta.get("date_from"),
        date_to=meta.get("date_to"),
        books_mode=meta.get("books_mode"),
        branch_id=meta.get("branch_id"),
    )
    printed = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<title>{_esc(report_title)}</title>
<style>{ODOO_REPORT_CSS}</style>
</head><body>
<div class="o_report_page">
  <div class="o_company">
    <div class="o_company_name">{_esc(business_name)}</div>
    <div class="o_company_meta">{addr}{tax}</div>
  </div>
  <div class="o_report_title">{_esc(report_title)}</div>
  {filt}
  {body_html}
  <div class="o_footer"><span>{printed}</span><span>TZS</span></div>
</div>
</body></html>"""
