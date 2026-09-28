"""Add partial/full refund columns on sales."""

from __future__ import annotations

import logging

from sqlalchemy import text

from app.config import settings
from app.database import engine

logger = logging.getLogger(__name__)


def _is_postgres() -> bool:
    return settings.async_database_url.startswith("postgresql")


async def migrate_sale_refund_columns() -> None:
    if not _is_postgres():
        return

    columns = [
        ("refunded_total", "DOUBLE PRECISION NOT NULL DEFAULT 0"),
        ("refunds", "JSON NOT NULL DEFAULT '[]'::json"),
        ("refunded_at", "TIMESTAMP WITH TIME ZONE"),
        ("refund_reason", "TEXT"),
        ("refunded_by", "VARCHAR(255)"),
    ]

    async with engine.begin() as conn:
        for name, ddl in columns:
            exists = await conn.scalar(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'sales' AND column_name = :col
                    )
                    """
                ),
                {"col": name},
            )
            if exists:
                continue
            logger.info("Adding sales.%s", name)
            await conn.execute(text(f"ALTER TABLE sales ADD COLUMN {name} {ddl}"))
