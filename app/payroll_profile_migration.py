"""Add hr_payroll_contracts.profile_json on existing SQLite/Postgres DBs."""

from sqlalchemy import text

from app.database import engine


async def migrate_payroll_contract_profile_column() -> None:
    async with engine.begin() as conn:
        dialect = conn.dialect.name
        if dialect == "sqlite":
            result = await conn.execute(text("PRAGMA table_info(hr_payroll_contracts)"))
            cols = {row[1] for row in result.fetchall()}
            if "profile_json" not in cols:
                await conn.execute(
                    text("ALTER TABLE hr_payroll_contracts ADD COLUMN profile_json TEXT DEFAULT '{}'")
                )
        else:
            try:
                await conn.execute(
                    text("ALTER TABLE hr_payroll_contracts ADD COLUMN profile_json TEXT DEFAULT '{}'")
                )
            except Exception:
                pass
