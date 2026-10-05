"""QWeb-style HTML for accounting reports (print / PDF via browser)."""

from __future__ import annotations

from typing import Any

from app.services.odoo_report_layout import payload_to_odoo_body_html, render_odoo_report_html


def render_report_html(
    *,
    business_name: str,
    report_title: str,
    meta: dict[str, Any],
    body_html: str,
    tax_id: str | None = None,
    address_lines: list[str] | None = None,
) -> str:
    return render_odoo_report_html(
        business_name=business_name,
        report_title=report_title,
        meta=meta,
        body_html=body_html,
        tax_id=tax_id,
        address_lines=address_lines,
    )


def payload_to_body_html(payload: dict[str, Any]) -> str:
    return payload_to_odoo_body_html(payload)
