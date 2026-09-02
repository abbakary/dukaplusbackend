"""One-time PostgreSQL enum migration: free_starter → starter."""

from __future__ import annotations

import logging

from sqlalchemy import text

from app.config import settings
from app.database import engine

logger = logging.getLogger(__name__)

_ENUM_NAME = "saasplantier"


def _is_postgres() -> bool:
    return settings.async_database_url.startswith("postgresql")


async def migrate_plan_tier_enum() -> None:
    """Ensure `starter` exists on the PG enum and remap legacy `free_starter` rows."""
    if not _is_postgres():
        return

    async with engine.begin() as conn:
        enum_exists = await conn.scalar(
            text("SELECT EXISTS (SELECT 1 FROM pg_type WHERE typname = :name)"),
            {"name": _ENUM_NAME},
        )
        if not enum_exists:
            return

        has_starter = await conn.scalar(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_type t
                    JOIN pg_enum e ON t.oid = e.enumtypid
                    WHERE t.typname = :name AND e.enumlabel = 'starter'
                )
                """
            ),
            {"name": _ENUM_NAME},
        )
        if not has_starter:
            logger.info("Adding 'starter' to PostgreSQL enum %s", _ENUM_NAME)
            try:
                await conn.execute(text(f"ALTER TYPE {_ENUM_NAME} ADD VALUE IF NOT EXISTS 'starter'"))
            except Exception:
                await conn.execute(text(f"ALTER TYPE {_ENUM_NAME} ADD VALUE 'starter'"))

    # Separate transaction: commit after ADD VALUE before the new label can be used.
    async with engine.begin() as conn:
        for stmt in (
            "UPDATE tenants SET plan = 'starter' WHERE plan::text = 'free_starter'",
            "UPDATE subscription_payments SET plan = 'starter' WHERE plan::text = 'free_starter'",
            "UPDATE platform_plans SET tier = 'starter' WHERE tier::text = 'free_starter'",
            "DELETE FROM platform_plans WHERE tier::text = 'free_starter'",
        ):
            try:
                result = await conn.execute(text(stmt))
                if result.rowcount:
                    logger.info("Plan tier migration: %s (%s row(s))", stmt.split()[0], result.rowcount)
            except Exception:
                logger.debug("Plan tier migration skipped statement: %s", stmt, exc_info=True)
