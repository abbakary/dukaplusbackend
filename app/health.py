"""Health and readiness probes for deployment platforms."""

from datetime import UTC, datetime

from sqlalchemy import func, select, text

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import Tenant


async def check_database() -> dict:
    started = datetime.now(UTC)
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
            tenants = await db.scalar(select(func.count(Tenant.id)))
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
