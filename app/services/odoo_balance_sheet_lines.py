"""Odoo-style IFRS balance sheet line hierarchy (Active / Passive)."""

from __future__ import annotations

from typing import Any


def _bal(tb: dict[str, float], code: str) -> float:
    return float(tb.get(code) or 0.0)


def build_odoo_balance_sheet_lines(
    *,
    bs: dict[str, Any],
    net_profit: float,
    tb_by_code: dict[str, float] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    """Map operational / ledger balances into Odoo PDF-style hierarchy."""
    tb = tb_by_code or {}
    cash = float(bs.get("cash") or 0) + _bal(tb, "1110")
    if not cash:
        cash = _bal(tb, "1000") + _bal(tb, "1100")
    trade_recv = float(bs.get("receivables") or 0) or _bal(tb, "1200")
    inventory = float(bs.get("inventory") or 0) or _bal(tb, "1300") + _bal(tb, "1310")
    input_vat = _bal(tb, "2210")
    other_ca = max(0.0, _bal(tb, "1250") + _bal(tb, "1310") + input_vat)
    if other_ca < 0.01 and input_vat:
        other_ca = input_vat

    non_current_assets = max(
        0.0,
        sum(v for c, v in tb.items() if c.startswith("15") or c.startswith("16")),
    )
    current_assets = cash + trade_recv + inventory + other_ca
    total_active = round(current_assets + non_current_assets, 2)

    share_capital = _bal(tb, "3000")
    reserves = _bal(tb, "3010")
    retained = _bal(tb, "3100")
    current_year = round(float(net_profit), 2)
    if abs(retained) < 0.01:
        retained = round(float(bs.get("equity_estimate") or 0) - current_year - share_capital - reserves, 2)
    total_equity = round(share_capital + reserves + retained + current_year, 2)

    trade_pay = float(bs.get("payables") or 0) or _bal(tb, "3110") + _bal(tb, "2100") + _bal(tb, "2000")
    vat_pay = float(bs.get("vat_payable") or 0) or _bal(tb, "2200")
    other_liab = max(0.0, vat_pay + _bal(tb, "2300"))
    if other_liab < 0.01 and vat_pay:
        other_liab = vat_pay
    current_liab = round(trade_pay + other_liab, 2)
    non_current_liab = 0.0
    total_liabilities = round(current_liab + non_current_liab, 2)
    total_passive = round(total_equity + total_liabilities, 2)

    lines: list[dict[str, Any]] = [
        {"id": "active", "name": "Active", "level": 0, "amount": total_active, "style": "section_total", "side": "active"},
        {"id": "nca_hd", "name": "Non currents assets", "level": 1, "amount": non_current_assets, "style": "header", "side": "active"},
        {"id": "ppe", "name": "Property, plant and equipment and Investment Property", "level": 2, "amount": 0, "style": "line", "side": "active"},
        {"id": "goodwill", "name": "Goodwill", "level": 2, "amount": 0, "style": "line", "side": "active"},
        {"id": "cap_advances", "name": "Advances for Capital Assets", "level": 2, "amount": 0, "style": "line", "side": "active"},
        {"id": "intangible", "name": "Intangible assets", "level": 2, "amount": 0, "style": "line", "side": "active"},
        {"id": "investments", "name": "Investments", "level": 2, "amount": 0, "style": "line", "side": "active"},
        {"id": "inv_assoc", "name": "Investments in associate", "level": 2, "amount": 0, "style": "line", "side": "active"},
        {"id": "inv_sale", "name": "Available for sale investments", "level": 2, "amount": 0, "style": "line", "side": "active"},
        {"id": "recv_nca", "name": "Receivables and other non-current asstes", "level": 2, "amount": 0, "style": "line", "side": "active"},
        {"id": "def_tax_a", "name": "Deferred tax assets", "level": 2, "amount": 0, "style": "line", "side": "active"},
        {"id": "ca_hd", "name": "Currents assets", "level": 1, "amount": round(current_assets, 2), "style": "header", "side": "active"},
        {"id": "inventory", "name": "Inventories", "level": 2, "amount": round(inventory, 2), "style": "line", "side": "active"},
        {"id": "trade_recv", "name": "Trade receivables", "level": 2, "amount": round(trade_recv, 2), "style": "line", "side": "active"},
        {"id": "other_ca", "name": "Other current assets", "level": 2, "amount": round(other_ca, 2), "style": "line", "side": "active"},
        {"id": "tax_asset", "name": "Income tax assets", "level": 2, "amount": 0, "style": "line", "side": "active"},
        {"id": "inv_fin", "name": "Investments and financial receivables", "level": 2, "amount": 0, "style": "line", "side": "active"},
        {"id": "cash", "name": "Cash and cash equivalents", "level": 2, "amount": round(cash, 2), "style": "line", "side": "active"},
        {"id": "passive", "name": "Passive", "level": 0, "amount": total_passive, "style": "section_total", "side": "passive"},
        {"id": "equity_hd", "name": "Equity", "level": 1, "amount": total_equity, "style": "header", "side": "passive"},
        {"id": "share_cap", "name": "Share capital", "level": 2, "amount": round(share_capital, 2), "style": "line", "side": "passive"},
        {"id": "reserves", "name": "Reserves", "level": 2, "amount": round(reserves, 2), "style": "line", "side": "passive"},
        {"id": "retained", "name": "Retained earnings", "level": 2, "amount": round(retained, 2), "style": "line", "side": "passive"},
        {"id": "cy_earnings", "name": "Current Year Earnings", "level": 2, "amount": current_year, "style": "line", "side": "passive"},
        {"id": "liab_hd", "name": "Liabilities", "level": 1, "amount": total_liabilities, "style": "header", "side": "passive"},
        {"id": "ncl_hd", "name": "Non current Liabilities", "level": 2, "amount": non_current_liab, "style": "header", "side": "passive"},
        {"id": "lt_loans", "name": "Interest-bearing loans and short term borrowings", "level": 3, "amount": 0, "style": "line", "side": "passive"},
        {"id": "emp_liab", "name": "Employee benefits liabilities", "level": 3, "amount": 0, "style": "line", "side": "passive"},
        {"id": "provisions_nc", "name": "Provisions", "level": 3, "amount": 0, "style": "line", "side": "passive"},
        {"id": "def_tax_l", "name": "Deferred tax liabilities", "level": 3, "amount": 0, "style": "line", "side": "passive"},
        {"id": "cl_hd", "name": "Current Liabilities", "level": 2, "amount": current_liab, "style": "header", "side": "passive"},
        {"id": "bank_od", "name": "Banks overdrafts and short-term borrowings", "level": 3, "amount": 0, "style": "line", "side": "passive"},
        {"id": "st_loans", "name": "Interest-bearing loans and short term borrowings", "level": 3, "amount": 0, "style": "line", "side": "passive"},
        {"id": "trade_pay", "name": "Trade payables", "level": 3, "amount": round(trade_pay, 2), "style": "header", "side": "passive"},
        {"id": "trade_pay_3110", "name": "3110 Trade Payables", "level": 4, "amount": round(trade_pay, 2), "style": "line", "side": "passive"},
        {"id": "provisions_c", "name": "Provisions", "level": 3, "amount": 0, "style": "line", "side": "passive"},
        {"id": "tax_liab", "name": "Income tax liabilities", "level": 3, "amount": 0, "style": "line", "side": "passive"},
        {"id": "other_liab", "name": "Other liabilities", "level": 3, "amount": round(other_liab, 2), "style": "header", "side": "passive"},
    ]
    totals = {
        "active": total_active,
        "passive": total_passive,
        "equity": total_equity,
        "liabilities": total_liabilities,
    }
    return lines, totals
