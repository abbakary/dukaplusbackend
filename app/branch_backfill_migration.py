"""Assign legacy NULL branch_id rows to each tenant's HQ branch."""

from __future__ import annotations

import logging

from sqlalchemy import text

from app.config import settings
from app.database import engine

logger = logging.getLogger(__name__)


def _is_postgres() -> bool:
    return settings.async_database_url.startswith("postgresql")


async def backfill_branch_ids() -> None:
    if not _is_postgres():
        return

    statements = (
        """
        UPDATE products p
        SET branch_id = b.id
        FROM branches b
        WHERE p.branch_id IS NULL
          AND p.tenant_id = b.tenant_id
          AND b.branch_type = 'main_hq'
        """,
        """
        UPDATE products p
        SET branch_id = b.id
        FROM (
            SELECT DISTINCT ON (tenant_id) id, tenant_id
            FROM branches
            ORDER BY tenant_id, created_at
        ) b
        WHERE p.branch_id IS NULL AND p.tenant_id = b.tenant_id
        """,
        """
        UPDATE sales s
        SET branch_id = p.branch_id
        FROM products p
        WHERE s.branch_id IS NULL
          AND s.items IS NOT NULL
          AND json_array_length(s.items::json) > 0
          AND (s.items::json->0->>'product_id') = p.id
          AND p.branch_id IS NOT NULL
        """,
        """
        UPDATE sales s
        SET branch_id = b.id
        FROM branches b
        WHERE s.branch_id IS NULL
          AND s.tenant_id = b.tenant_id
          AND b.branch_type = 'main_hq'
        """,
        """
        UPDATE customers c
        SET branch_id = b.id
        FROM branches b
        WHERE c.branch_id IS NULL
          AND c.tenant_id = b.tenant_id
          AND b.branch_type = 'main_hq'
        """,
    )

    async with engine.begin() as conn:
        for stmt in statements:
            try:
                result = await conn.execute(text(stmt))
                if result.rowcount:
                    logger.info("Branch backfill (%s rows): %s", result.rowcount, stmt.split()[1])
            except Exception:
                logger.debug("Branch backfill skipped: %s", stmt.split()[1], exc_info=True)
