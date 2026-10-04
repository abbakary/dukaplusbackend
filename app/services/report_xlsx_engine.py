"""Zalongwa-style XLSX export from run_odoo_report payloads."""

from __future__ import annotations

import io
from typing import Any

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False


def _header_row(ws, row: int, titles: list[str]) -> None:
    bold = Font(bold=True)
    for col, title in enumerate(titles, start=1):
        cell = ws.cell(row=row, column=col, value=title)
        cell.font = bold


def report_payload_to_xlsx_bytes(report_key: str, payload: dict[str, Any], company_name: str) -> bytes:
    if not HAS_OPENPYXL:
        raise RuntimeError("openpyxl is required for XLSX export (pip install openpyxl)")

    wb = Workbook()
    ws = wb.active
    ws.title = report_key[:31]
    ws.cell(row=1, column=1, value=company_name)
    ws.cell(row=2, column=1, value=f"Report: {report_key}")
    ws.cell(row=3, column=1, value=f"Period: {payload.get('date_from')} — {payload.get('date_to')}")

    rtype = str(payload.get("type") or "")
    start = 5

    if rtype == "trial_balance":
        _header_row(ws, start, ["Code", "Account", "Initial", "Debit", "Credit", "End"])
        for i, row in enumerate(payload.get("rows") or [], start=start + 1):
            ws.cell(row=i, column=1, value=row.get("code"))
            ws.cell(row=i, column=2, value=row.get("name"))
            ws.cell(row=i, column=3, value=row.get("initial_balance"))
            ws.cell(row=i, column=4, value=row.get("debit"))
            ws.cell(row=i, column=5, value=row.get("credit"))
            ws.cell(row=i, column=6, value=row.get("end_balance"))

    elif rtype == "general_ledger":
        row_i = start
        for acct in payload.get("accounts") or []:
            ws.cell(row=row_i, column=1, value=f"{acct.get('code')} {acct.get('name')}")
            row_i += 1
            _header_row(ws, row_i, ["Date", "Reference", "Label", "Debit", "Credit"])
            row_i += 1
            for ln in acct.get("lines") or []:
                ws.cell(row=row_i, column=1, value=ln.get("date"))
                ws.cell(row=row_i, column=2, value=ln.get("reference"))
                ws.cell(row=row_i, column=3, value=ln.get("label"))
                ws.cell(row=row_i, column=4, value=ln.get("debit"))
                ws.cell(row=row_i, column=5, value=ln.get("credit"))
                row_i += 1
            row_i += 1

    elif rtype == "open_items":
        _header_row(ws, start, ["Date", "Move", "Partner", "Account", "Open amount"])
        for i, row in enumerate(payload.get("rows") or [], start=start + 1):
            ws.cell(row=i, column=1, value=row.get("date"))
            ws.cell(row=i, column=2, value=row.get("move_name"))
            ws.cell(row=i, column=3, value=row.get("partner_name"))
            ws.cell(row=i, column=4, value=row.get("account_code"))
            ws.cell(row=i, column=5, value=row.get("amount"))

    elif rtype in ("vat", "tax"):
        _header_row(ws, start, ["Description", "Amount"])
        for i, row in enumerate(payload.get("rows") or [], start=start + 1):
            ws.cell(row=i, column=1, value=row.get("label_en") or row.get("label"))
            ws.cell(row=i, column=2, value=row.get("amount"))

    elif rtype == "hierarchy":
        _header_row(ws, start, ["Line", "Amount"])
        data = payload.get("data") or {}
        lines = payload.get("lines") or data.get("lines") or []
        for i, row in enumerate(lines, start=start + 1):
            ws.cell(row=i, column=1, value=row.get("name"))
            ws.cell(row=i, column=2, value=row.get("amount"))

    else:
        ws.cell(row=start, column=1, value="Export not implemented for this report type")

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
