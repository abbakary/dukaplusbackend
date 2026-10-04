"""Odoo-style PDF/HTML for vendor bills and customer quotations."""

from __future__ import annotations

import html
import json
from typing import Any


def _esc(s: str) -> str:
    return html.escape(str(s or ""))


def _money(n: float) -> str:
    return f"{float(n or 0):,.2f}"


def _company_block(company: dict[str, Any]) -> str:
    return f"""
    <div class="company">
      <div class="company-name">{_esc(company.get('name', 'Duka+'))}</div>
      <div class="company-meta">
        {_esc(company.get('address', ''))}
        {f"<br/>TIN: {_esc(company['tin'])}" if company.get('tin') else ''}
        {f" · VRN: {_esc(company['vrn'])}" if company.get('vrn') else ''}
      </div>
    </div>"""


def render_vendor_bill_html(*, company: dict[str, Any], bill: dict[str, Any]) -> str:
    lines = bill.get("lines") or []
    if isinstance(lines, str):
        lines = json.loads(lines or "[]")
    rows = ""
    for ln in lines:
        if not isinstance(ln, dict):
            continue
        qty = float(ln.get("quantity") or 0)
        pu = float(ln.get("price_unit") or 0)
        amt = float(ln.get("amount") or qty * pu)
        tax = float(ln.get("tax_rate") or 0)
        rows += f"""
        <tr>
          <td>{_esc(ln.get('label') or ln.get('product_name') or '')}</td>
          <td class="num">{_esc(ln.get('account_code') or '')}</td>
          <td class="num">{qty:,.2f}</td>
          <td class="num">{_money(pu)}</td>
          <td class="num">{tax:,.0f}%</td>
          <td class="num">{_money(amt)}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"/>
<title>{_esc(bill.get('name', 'Bill'))}</title>
<style>
  body {{ font-family: Helvetica, Arial, sans-serif; font-size: 12px; color: #323130; margin: 28px; }}
  .header {{ display: flex; justify-content: space-between; border-bottom: 2px solid #714B67; padding-bottom: 12px; margin-bottom: 16px; }}
  .doc-title {{ font-size: 22px; font-weight: bold; color: #714B67; }}
  .partner {{ margin: 12px 0; padding: 10px; background: #F8F9FA; border-radius: 4px; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
  th {{ text-align: left; border-bottom: 2px solid #EDEBE9; padding: 6px; font-size: 10px; text-transform: uppercase; }}
  td {{ padding: 6px; border-bottom: 1px solid #F3F2F1; }}
  td.num, th.num {{ text-align: right; }}
  .totals {{ margin-top: 16px; width: 280px; margin-left: auto; }}
  .totals div {{ display: flex; justify-content: space-between; padding: 4px 0; }}
  .totals .grand {{ font-weight: bold; font-size: 14px; border-top: 2px solid #714B67; margin-top: 6px; padding-top: 6px; }}
  .state {{ float: right; font-size: 11px; font-weight: bold; color: #605E5C; }}
  @media print {{ body {{ margin: 12px; }} }}
</style></head><body>
  <div class="header">
    {_company_block(company)}
    <div style="text-align:right">
      <div class="doc-title">{_esc(bill.get('name', 'Vendor Bill'))}</div>
      <div class="state">{_esc(bill.get('state', ''))} · {_esc(bill.get('payment_state', ''))}</div>
      <div>{_esc(bill.get('bill_date', ''))}</div>
      <div>Due: {_esc(bill.get('due_date', ''))}</div>
      <div>Ref: {_esc(bill.get('vendor_bill_ref', ''))}</div>
    </div>
  </div>
  <div class="partner">
    <strong>Vendor</strong><br/>{_esc(bill.get('vendor_name', ''))}
  </div>
  <table>
    <thead><tr>
      <th>Description</th><th class="num">Account</th><th class="num">Qty</th>
      <th class="num">Unit price</th><th class="num">Tax</th><th class="num">Amount</th>
    </tr></thead>
    <tbody>{rows}</tbody>
  </table>
  <div class="totals">
    <div><span>Untaxed</span><span>{_money(float(bill.get('amount_untaxed') or 0))} TZS</span></div>
    <div><span>Tax</span><span>{_money(float(bill.get('amount_tax') or 0))} TZS</span></div>
    <div class="grand"><span>Total</span><span>{_money(float(bill.get('amount_total') or 0))} TZS</span></div>
    <div><span>Amount due</span><span>{_money(float(bill.get('amount_residual') or bill.get('amount_total') or 0))} TZS</span></div>
  </div>
  {f"<p style='margin-top:20px;font-size:11px;color:#605E5C'>{_esc(bill.get('notes', ''))}</p>" if bill.get('notes') else ''}
</body></html>"""


def render_quotation_html(*, company: dict[str, Any], quotation: dict[str, Any]) -> str:
    lines = quotation.get("lines") or []
    if isinstance(lines, str):
        lines = json.loads(lines or "[]")
    rows = ""
    for ln in lines:
        if not isinstance(ln, dict):
            continue
        qty = float(ln.get("quantity") or 0)
        pu = float(ln.get("price_unit") or 0)
        sub = float(ln.get("amount") or qty * pu)
        tax = float(ln.get("tax_rate") or 0)
        tax_amt = sub * tax / 100
        rows += f"""
        <tr>
          <td>{_esc(ln.get('product_name') or ln.get('label') or '')}</td>
          <td class="num">{_esc(ln.get('sku') or '')}</td>
          <td class="num">{qty:,.2f}</td>
          <td class="num">{_money(pu)}</td>
          <td class="num">{tax:,.0f}%</td>
          <td class="num">{_money(sub + tax_amt)}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"/>
<title>{_esc(quotation.get('name', 'Quotation'))}</title>
<style>
  body {{ font-family: Helvetica, Arial, sans-serif; font-size: 12px; color: #323130; margin: 28px; }}
  .header {{ display: flex; justify-content: space-between; border-bottom: 2px solid #714B67; padding-bottom: 12px; margin-bottom: 16px; }}
  .doc-title {{ font-size: 22px; font-weight: bold; color: #714B67; }}
  .partner {{ margin: 12px 0; padding: 10px; background: #F8F9FA; border-radius: 4px; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
  th {{ text-align: left; border-bottom: 2px solid #EDEBE9; padding: 6px; font-size: 10px; text-transform: uppercase; }}
  td {{ padding: 6px; border-bottom: 1px solid #F3F2F1; }}
  td.num, th.num {{ text-align: right; }}
  .totals {{ margin-top: 16px; width: 280px; margin-left: auto; }}
  .totals div {{ display: flex; justify-content: space-between; padding: 4px 0; }}
  .totals .grand {{ font-weight: bold; font-size: 14px; border-top: 2px solid #714B67; margin-top: 6px; padding-top: 6px; }}
  @media print {{ body {{ margin: 12px; }} }}
</style></head><body>
  <div class="header">
    {_company_block(company)}
    <div style="text-align:right">
      <div class="doc-title">Quotation</div>
      <div>{_esc(quotation.get('name', ''))}</div>
      <div>Date: {_esc(quotation.get('quotation_date', ''))}</div>
      <div>Valid until: {_esc(quotation.get('validity_date', ''))}</div>
      <div>Payment: {_esc(quotation.get('payment_terms', 'immediate'))}</div>
      <div>Status: {_esc(quotation.get('state', 'draft'))}</div>
    </div>
  </div>
  <div class="partner">
    <strong>Customer</strong><br/>{_esc(quotation.get('customer_name', ''))}
  </div>
  <table>
    <thead><tr>
      <th>Product</th><th class="num">SKU</th><th class="num">Qty</th>
      <th class="num">Unit price</th><th class="num">VAT</th><th class="num">Total</th>
    </tr></thead>
    <tbody>{rows}</tbody>
  </table>
  <div class="totals">
    <div><span>Untaxed</span><span>{_money(float(quotation.get('amount_untaxed') or 0))} TZS</span></div>
    <div><span>Tax</span><span>{_money(float(quotation.get('amount_tax') or 0))} TZS</span></div>
    <div class="grand"><span>Total</span><span>{_money(float(quotation.get('amount_total') or 0))} TZS</span></div>
  </div>
  {f"<p style='margin-top:16px;font-size:11px'><strong>Terms</strong><br/>{_esc(quotation.get('terms', ''))}</p>" if quotation.get('terms') else ''}
</body></html>"""
