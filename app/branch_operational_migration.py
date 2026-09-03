"""Add branch_id to calendar_events and purchase_orders for branch isolation."""

from __future__ import annotations

import logging

from sqlalchemy import text

from app.config import settings
from app.database import engine

logger = logging.getLogger(__name__)


def _is_postgres() -> bool:
    return settings.async_database_url.startswith("postgresql")


async def migrate_operational_branch_columns() -> None:
    if not _is_postgres():
        return

    async with engine.begin() as conn:
        for table in ("calendar_events", "purchase_orders"):
            exists = await conn.scalar(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = :tbl AND column_name = 'branch_id'
                    )
                    """
                ),
                {"tbl": table},
            )
            if not exists:
                logger.info("Adding %s.branch_id", table)
                await conn.execute(
                    text(
                        f"ALTER TABLE {table} ADD COLUMN branch_id VARCHAR(36) REFERENCES branches(id)"
                    )
                )

        await conn.execute(
            text(
                """
                UPDATE calendar_events ce
                SET branch_id = b.id
                FROM branches b
                WHERE ce.branch_id IS NULL
                  AND ce.tenant_id = b.tenant_id
                  AND b.branch_type = 'main_hq'
                """
            )
        )
        await conn.execute(
            text(
                """
                UPDATE purchase_orders po
                SET branch_id = p.branch_id
                FROM products p
                WHERE po.branch_id IS NULL
                  AND po.items IS NOT NULL
                  AND json_array_length(po.items::json) > 0
                  AND (po.items::json->0->>'product_id') = p.id
                  AND p.branch_id IS NOT NULL
                """
            )
        )
        await conn.execute(
            text(
                """
                UPDATE purchase_orders po
                SET branch_id = b.id
                FROM branches b
                WHERE po.branch_id IS NULL
                  AND po.tenant_id = b.tenant_id
                  AND b.branch_type = 'main_hq'
                """
            )
        )
