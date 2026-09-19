"""Default chart of accounts for Tanzanian retail / SME shops."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.accounting import LedgerAccount

DEFAULT_ACCOUNTS: list[tuple[str, str, str]] = [
    ("1000", "Cash & Mobile Money", "asset"),
    ("1100", "Bank", "asset"),
    ("1200", "Accounts Receivable", "asset"),
    ("1300", "Inventory", "asset"),
    ("2000", "Accounts Payable", "liability"),
    ("2100", "VAT Payable (TRA)", "liability"),
    ("2200", "NSSF Payable", "liability"),
    ("3000", "Owner's Equity", "equity"),
    ("4000", "Sales Revenue", "income"),
    ("4100", "Other Income", "income"),
    ("5000", "Cost of Goods Sold", "expense"),
    ("6000", "Operating Expenses", "expense"),
    ("6100", "Rent & Utilities", "expense"),
    ("6200", "Salaries & Wages", "expense"),
    ("6300", "Marketing", "expense"),
]


async def ensure_default_chart(db: AsyncSession, tenant_id: str) -> list[LedgerAccount]:
    result = await db.execute(select(LedgerAccount).where(LedgerAccount.tenant_id == tenant_id))
    existing = result.scalars().all()
    if existing:
        return list(existing)

    rows: list[LedgerAccount] = []
    for code, name, account_type in DEFAULT_ACCOUNTS:
        row = LedgerAccount(tenant_id=tenant_id, code=code, name=name, account_type=account_type, is_active=True)
        db.add(row)
        rows.append(row)
    await db.flush()
    return rows
