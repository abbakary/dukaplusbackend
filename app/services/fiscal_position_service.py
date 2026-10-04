"""Odoo fiscal positions — default TZ VAT mapping per tenant."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.accounting import AccFiscalPosition


async def ensure_default_fiscal_positions(db: AsyncSession, tenant_id: str) -> list[AccFiscalPosition]:
    existing = (await db.execute(select(AccFiscalPosition).where(AccFiscalPosition.tenant_id == tenant_id))).scalars().all()
    if existing:
        return list(existing)
    defaults = [
        ("standard", "Tanzania Standard (18% VAT)", "2100", "1310", 18.0),
        ("export", "Export / Zero-rated", "2100", "1310", 0.0),
        ("exempt", "VAT Exempt", "2100", "1310", 0.0),
    ]
    out: list[AccFiscalPosition] = []
    for code, name, out_acct, in_acct, rate in defaults:
        fp = AccFiscalPosition(
            tenant_id=tenant_id,
            code=code,
            name=name,
            vat_output_account=out_acct,
            vat_input_account=in_acct,
            default_sale_tax_rate=rate,
            auto_apply=code == "standard",
        )
        db.add(fp)
        out.append(fp)
    await db.flush()
    return out


async def resolve_fiscal_position(
    db: AsyncSession,
    tenant_id: str,
    *,
    fiscal_position_id: str | None = None,
    partner_is_export: bool = False,
) -> AccFiscalPosition:
    await ensure_default_fiscal_positions(db, tenant_id)
    if fiscal_position_id:
        fp = await db.get(AccFiscalPosition, fiscal_position_id)
        if fp and fp.tenant_id == tenant_id:
            return fp
    rows = (
        await db.execute(
            select(AccFiscalPosition).where(AccFiscalPosition.tenant_id == tenant_id, AccFiscalPosition.active.is_(True))
        )
    ).scalars().all()
    by_code = {r.code: r for r in rows}
    if partner_is_export and by_code.get("export"):
        return by_code["export"]
    for r in rows:
        if r.auto_apply:
            return r
    return rows[0] if rows else by_code["standard"]
