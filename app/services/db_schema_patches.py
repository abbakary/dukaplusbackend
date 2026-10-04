"""Idempotent PostgreSQL column patches for production DBs created before new accounting fields."""

from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_lock = asyncio.Lock()
_accounting_patched = False

# Idempotent DDL for DBs deployed before Odoo-style accounting layer.
SCHEMA_PATCHES: tuple[str, ...] = (
    "ALTER TABLE vendor_bills ADD COLUMN IF NOT EXISTS acc_move_id VARCHAR(36)",
    "ALTER TABLE vendor_bills ADD COLUMN IF NOT EXISTS purchase_order_id VARCHAR(36)",
    "ALTER TABLE sales ADD COLUMN IF NOT EXISTS acc_move_id VARCHAR(36)",
    "CREATE INDEX IF NOT EXISTS ix_vendor_bills_acc_move_id ON vendor_bills (acc_move_id)",
    "CREATE INDEX IF NOT EXISTS ix_sales_acc_move_id ON sales (acc_move_id)",
)


async def ensure_accounting_schema(db: AsyncSession) -> None:
    """Add ledger link columns (acc_move_id) on sales & vendor bills when missing."""
    global _accounting_patched
    if _accounting_patched:
        return
    async with _lock:
        if _accounting_patched:
            return
        for sql in SCHEMA_PATCHES:
            await db.execute(text(sql))
        _accounting_patched = True


async def apply_schema_patches_on_startup(engine) -> None:
    """Call once from FastAPI lifespan so /sales and other routes work before accounting is hit."""
    from sqlalchemy import text as sql_text

    async with engine.begin() as conn:
        for sql in SCHEMA_PATCHES:
            await conn.execute(sql_text(sql))
