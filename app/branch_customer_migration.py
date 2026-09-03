"""Add customers.branch_id for per-branch CRM isolation."""

from __future__ import annotations

import logging

from sqlalchemy import text

from app.config import settings
from app.database import engine

logger = logging.getLogger(__name__)


def _is_postgres() -> bool:
    return settings.async_database_url.startswith("postgresql")


async def migrate_customer_branch_column() -> None:
    if not _is_postgres():
        return
    async with engine.begin() as conn:
        exists = await conn.scalar(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'customers' AND column_name = 'branch_id'
                )
                """
            )
        )
        if exists:
            return
        logger.info("Adding customers.branch_id for branch isolation")
        await conn.execute(
            text("ALTER TABLE customers ADD COLUMN branch_id VARCHAR(36) REFERENCES branches(id)")
        )
