"""Idempotent column/index patches for SQLite (local) and PostgreSQL (production)."""

from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from app.config import settings

_lock = asyncio.Lock()
_accounting_patched = False

# (table, column, SQL type, optional default SQL fragment e.g. "'immediate'")
_COLUMN_PATCHES: tuple[tuple[str, str, str, str | None], ...] = (
    ("vendor_bills", "acc_move_id", "VARCHAR(36)", None),
    ("vendor_bills", "purchase_order_id", "VARCHAR(36)", None),
    ("vendor_bills", "branch_id", "VARCHAR(36)", None),
    ("sales", "acc_move_id", "VARCHAR(36)", None),
    ("sale_quotations", "branch_id", "VARCHAR(36)", None),
    ("sale_quotations", "quotation_date", "DATE", None),
    ("sale_quotations", "payment_terms", "VARCHAR(40)", "'immediate'"),
)

_INDEX_PATCHES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS ix_vendor_bills_acc_move_id ON vendor_bills (acc_move_id)",
    "CREATE INDEX IF NOT EXISTS ix_sales_acc_move_id ON sales (acc_move_id)",
    "CREATE INDEX IF NOT EXISTS ix_vendor_bills_branch_id ON vendor_bills (branch_id)",
    "CREATE INDEX IF NOT EXISTS ix_sale_quotations_branch_id ON sale_quotations (branch_id)",
)


def _is_sqlite(dialect_name: str | None = None) -> bool:
    name = dialect_name or settings.async_database_url.split(":", 1)[0]
    return "sqlite" in name.lower()


async def _table_has_column(conn: AsyncConnection, table: str, column: str) -> bool:
    if _is_sqlite(conn.dialect.name):
        result = await conn.execute(text(f"PRAGMA table_info({table})"))
        return any(row[1] == column for row in result.fetchall())
    q = text(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_name = :table AND column_name = :column
        LIMIT 1
        """
    )
    row = (await conn.execute(q, {"table": table, "column": column})).first()
    return row is not None


async def _apply_column_patches(conn: AsyncConnection) -> None:
    dialect = conn.dialect.name
    sqlite = _is_sqlite(dialect)
    for table, column, col_type, default in _COLUMN_PATCHES:
        if await _table_has_column(conn, table, column):
            continue
        if sqlite:
            ddl = f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"
            if default is not None:
                ddl += f" DEFAULT {default}"
        else:
            ddl = f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"
            if default is not None:
                ddl += f" DEFAULT {default}"
        await conn.execute(text(ddl))


async def _apply_index_patches(conn: AsyncConnection) -> None:
    for sql in _INDEX_PATCHES:
        await conn.execute(text(sql))


async def _apply_all_patches(conn: AsyncConnection) -> None:
    await _apply_column_patches(conn)
    await _apply_index_patches(conn)


async def ensure_accounting_schema(db: AsyncSession) -> None:
    """Add ledger link columns when missing (safe on SQLite and PostgreSQL)."""
    global _accounting_patched
    if _accounting_patched:
        return
    async with _lock:
        if _accounting_patched:
            return
        conn = await db.connection()
        await _apply_all_patches(conn)
        _accounting_patched = True


async def apply_schema_patches_on_startup(engine: AsyncEngine) -> None:
    """Run once from FastAPI lifespan before routes that read sales/accounting."""
    global _accounting_patched
    async with engine.begin() as conn:
        await _apply_all_patches(conn)
    _accounting_patched = True
