"""Health and readiness probes for deployment platforms."""

from datetime import UTC, datetime

from sqlalchemy import func, select, text

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import Tenant, User, UserRole


async def check_database() -> dict:
    started = datetime.now(UTC)
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
            tenants = await db.scalar(select(func.count(Tenant.id)))
            super_admins = await db.scalar(
                select(func.count(User.id)).where(User.role == UserRole.super_admin)
            )
            bootstrap_email = settings.super_admin_email.strip().lower()
            bootstrap_exists = await db.scalar(
                select(func.count(User.id)).where(User.email == bootstrap_email)
            )
    except Exception as exc:
        elapsed_ms = (datetime.now(UTC) - started).total_seconds() * 1000
        return {
            "status": "error",
            "message": str(exc),
            "latency_ms": round(elapsed_ms, 1),
        }

    elapsed_ms = (datetime.now(UTC) - started).total_seconds() * 1000
    db_kind = "postgresql" if settings.async_database_url.startswith("postgresql") else "sqlite"
    return {
        "status": "ok",
        "engine": db_kind,
        "latency_ms": round(elapsed_ms, 1),
        "tenant_count": int(tenants or 0) if tenants is not None else None,
        "super_admin_count": int(super_admins or 0),
        "bootstrap_super_admin_exists": bool(bootstrap_exists),
        "bootstrap_super_admin_email": bootstrap_email,
    }


async def get_system_status() -> dict:
    db = await check_database()
    overall = "ok" if db["status"] == "ok" else "degraded"

    return {
        "status": overall,
        "app": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "timestamp": datetime.now(UTC).isoformat(),
        "checks": {
            "api": {"status": "ok"},
            "database": db,
        },
    }
