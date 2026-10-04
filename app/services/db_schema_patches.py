"""Idempotent PostgreSQL column patches for production DBs created before new accounting fields."""

from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_lock = asyncio.Lock()
_accounting_patched = False


async def ensure_accounting_schema(db: AsyncSession) -> None:
    """Add vendor_bills columns expected by Odoo-style bills (acc_move_id, etc.)."""
    global _accounting_patched
    if _accounting_patched:
        return
    async with _lock:
        if _accounting_patched:
            return
        statements = [
            "ALTER TABLE vendor_bills ADD COLUMN IF NOT EXISTS acc_move_id VARCHAR(36)",
            "ALTER TABLE vendor_bills ADD COLUMN IF NOT EXISTS purchase_order_id VARCHAR(36)",
            "CREATE INDEX IF NOT EXISTS ix_vendor_bills_acc_move_id ON vendor_bills (acc_move_id)",
        ]
        for sql in statements:
            await db.execute(text(sql))
        _accounting_patched = True
