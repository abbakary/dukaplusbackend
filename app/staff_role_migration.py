"""StaffRole on PostgreSQL: add HR to legacy enums and/or store roles as VARCHAR (no enum lock-in)."""

from __future__ import annotations

import logging

from sqlalchemy import text

from app.config import settings
from app.database import engine

logger = logging.getLogger(__name__)

_ENUM_NAME = "staffrole"


def _is_postgres() -> bool:
    return settings.async_database_url.startswith("postgresql")


async def _add_hr_to_pg_enum(conn) -> None:
    enum_exists = await conn.scalar(
        text("SELECT EXISTS (SELECT 1 FROM pg_type WHERE typname = :name)"),
        {"name": _ENUM_NAME},
    )
    if not enum_exists:
        return

    labels = (
        await conn.execute(
            text(
                """
                SELECT e.enumlabel
                FROM pg_type t
                JOIN pg_enum e ON t.oid = e.enumtypid
                WHERE t.typname = :enum_name
                ORDER BY e.enumsortorder
                """
            ),
            {"enum_name": _ENUM_NAME},
        )
    ).scalars().all()

    if not labels:
        return

    uses_title_case = any(lbl == "Cashier" or lbl == "Manager" for lbl in labels)
    to_add: list[str] = []
    if uses_title_case:
        if "HR" not in labels:
            to_add.append("HR")
    else:
        if "hr" not in labels and "HR" not in labels:
            to_add.append("hr")

    for label in to_add:
        await conn.execute(text(f"ALTER TYPE {_ENUM_NAME} ADD VALUE '{label}'"))
        logger.info("Added %s to PostgreSQL enum %s", label, _ENUM_NAME)


async def _convert_staff_role_to_varchar(conn) -> None:
    udt = await conn.scalar(
        text(
            """
            SELECT udt_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'staff_members'
              AND column_name = 'role'
            """
        )
    )
    if not udt or udt in ("varchar", "text", "bpchar"):
        return
    await conn.execute(
        text("ALTER TABLE staff_members ALTER COLUMN role TYPE VARCHAR(32) USING role::text")
    )
    logger.info("Converted staff_members.role from %s to VARCHAR(32)", udt)


async def migrate_staff_role_hr_enum() -> None:
    if not _is_postgres():
        return

    async with engine.begin() as conn:
        await _add_hr_to_pg_enum(conn)
        await _convert_staff_role_to_varchar(conn)
