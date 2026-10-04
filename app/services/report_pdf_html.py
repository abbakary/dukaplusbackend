"""QWeb-style HTML for accounting reports (print / PDF via browser)."""

from __future__ import annotations

import html
from typing import Any


def _esc(s: str) -> str:
    return html.escape(str(s or ""))


def render_report_html(
    *,
    business_name: str,
    report_title: str,
    meta: dict[str, Any],
    body_html: str,
) -> str:
  period = f"{meta.get('date_from', '')} — {meta.get('date_to', '')}"
  filters = meta.get("filters_html") or ""
  return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>{_esc(report_title)}</title>
  <style>
    body {{ font-family: "Segoe UI", Helvetica, Arial, sans-serif; font-size: 12px; color: #323130; margin: 24px; }}
    h1 {{ font-size: 20px; margin: 0 0 4px; color: #714B67; }}
    .meta {{ color: #605E5C; margin-bottom: 16px; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th {{ text-align: left; border-bottom: 2px solid #EDEBE9; padding: 6px 8px; font-size: 11px; text-transform: uppercase; }}
    td {{ padding: 5px 8px; border-bottom: 1px solid #F3F2F1; }}
    td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
    tr.total td {{ font-weight: bold; border-top: 2px solid #714B67; }}
    tr.section td {{ font-weight: 600; background: #F8F9FA; }}
    @media print {{ body {{ margin: 12px; }} }}
  </style>
</head>
<body>
  <h1>{_esc(business_name)}</h1>
  <div class="meta"><strong>{_esc(report_title)}</strong><br/>{ _esc(period) } · TZS · { _esc(meta.get('target_move', 'posted')) }</div>
  {filters}
  {body_html}
</body>
</html>"""


def payload_to_body_html(payload: dict[str, Any]) -> str:
    rtype = str(payload.get("type") or "")
    if rtype == "trial_balance":
        rows = payload.get("rows") or []
        lines = [
            "<table><thead><tr><th>Account</th><th class='num'>Initial</th><th class='num'>Debit</th>"
            "<th class='num'>Credit</th><th class='num'>End</th></tr></thead><tbody>"
        ]
        for r in rows:
            lines.append(
                f"<tr><td>{_esc(r.get('code'))} {_esc(r.get('name'))}</td>"
                f"<td class='num'>{r.get('initial_balance', 0):,.2f}</td>"
                f"<td class='num'>{r.get('debit', 0):,.2f}</td>"
                f"<td class='num'>{r.get('credit', 0):,.2f}</td>"
                f"<td class='num'>{r.get('end_balance', 0):,.2f}</td></tr>"
            )
        lines.append("</tbody></table>")
        return "\n".join(lines)

    if rtype == "hierarchy":
        data = payload.get("data") or {}
        lines_list = data.get("lines") or payload.get("lines") or []
        lines = ["<table><thead><tr><th>Line</th><th class='num'>Amount (TZS)</th></tr></thead><tbody>"]
        for r in lines_list:
            style = r.get("style") or ""
            cls = "section" if style in ("section", "title", "total") else ""
            lines.append(
                f"<tr class='{cls}'><td>{_esc(r.get('name'))}</td>"
                f"<td class='num'>{float(r.get('amount', 0)):,.2f}</td></tr>"
            )
        lines.append("</tbody></table>")
        return "\n".join(lines)

    if rtype == "open_items":
        rows = payload.get("rows") or []
        lines = [
            "<table><thead><tr><th>Date</th><th>Move</th><th>Partner</th><th>Label</th>"
            "<th>Account</th><th class='num'>Open</th></tr></thead><tbody>"
        ]
        for r in rows:
            lines.append(
                f"<tr><td>{_esc(r.get('date'))}</td><td>{_esc(r.get('move_name'))}</td>"
                f"<td>{_esc(r.get('partner_name'))}</td><td>{_esc(r.get('label'))}</td>"
                f"<td>{_esc(r.get('account_code'))}</td><td class='num'>{float(r.get('amount', 0)):,.2f}</td></tr>"
            )
        lines.append("</tbody></table>")
        return "\n".join(lines)

    if rtype == "vat":
        rows = payload.get("rows") or []
        lines = ["<table><thead><tr><th>Description</th><th class='num'>Amount (TZS)</th></tr></thead><tbody>"]
        for r in rows:
            label = r.get("label_en") or r.get("label") or ""
            lines.append(
                f"<tr><td>{_esc(label)}</td><td class='num'>{float(r.get('amount', 0)):,.2f}</td></tr>"
            )
        lines.append("</tbody></table>")
        return "\n".join(lines)

    return "<p>No printable layout for this report type.</p>"
