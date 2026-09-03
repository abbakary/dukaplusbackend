from contextlib import asynccontextmanager
from pathlib import Path
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from app.api.v1 import admin, ai, analytics, auth, billing, business, documents, extended, platform, tenant, tenant_settings, workplace
from app.config import settings
from app.database import init_db
from app.health import check_database, get_system_status
from app.branch_backfill_migration import backfill_branch_ids
from app.branch_customer_migration import migrate_customer_branch_column
from app.branch_operational_migration import migrate_operational_branch_columns
from app.plan_tier_migration import migrate_plan_tier_enum
from app.seed import seed_demo_data
from app.seed_plans import seed_platform_plans
from app.seed_provider_data import seed_provider_data
from app.seed_showcase import seed_platform_showcase

CONSOLE_HTML = (Path(__file__).resolve().parent / "static" / "console.html").read_text(encoding="utf-8")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await migrate_plan_tier_enum()
    await migrate_customer_branch_column()
    await migrate_operational_branch_columns()
    await backfill_branch_ids()
    try:
        await seed_demo_data()
    except Exception:
        logger.exception("Startup seed failed")
        raise
    await seed_platform_plans()
    if settings.seed_demo_data:
        await seed_provider_data()
    await seed_platform_showcase()
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Duka+ SaaS ERP API — Production backend for Tanzania retail, pharmacy, and multi-business management",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_origin_regex=r"https://([a-z0-9-]+\.)*vercel\.app|http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/v1")
app.include_router(business.router, prefix="/api/v1")
app.include_router(analytics.router, prefix="/api/v1")
app.include_router(tenant.router, prefix="/api/v1")
app.include_router(extended.router, prefix="/api/v1")
app.include_router(platform.router, prefix="/api/v1")
app.include_router(billing.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")
app.include_router(tenant_settings.router, prefix="/api/v1")
app.include_router(documents.router, prefix="/api/v1")
app.include_router(workplace.router, prefix="/api/v1")
app.include_router(ai.router, prefix="/api/ai")
app.include_router(ai.router, prefix="/api/v1/ai")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
@app.get("/admin-console", response_class=HTMLResponse, include_in_schema=False)
async def admin_console():
    """Status dashboard + super-admin account management UI."""
    return CONSOLE_HTML


@app.get("/api/health")
async def health():
    """Lightweight health probe for Railway / load balancers."""
    db = await check_database()
    status_code = 200 if db["status"] == "ok" else 503
    payload = {
        "status": "ok" if db["status"] == "ok" else "degraded",
        "app": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "database": db["status"],
    }
    return JSONResponse(content=payload, status_code=status_code)


@app.get("/api/health/detailed")
async def health_detailed():
    """Full system status with database latency and tenant count."""
    payload = await get_system_status()
    status_code = 200 if payload["status"] == "ok" else 503
    return JSONResponse(content=payload, status_code=status_code)


@app.get("/api/ready")
async def readiness():
    """Readiness probe — returns 503 until database is reachable."""
    db = await check_database()
    if db["status"] != "ok":
        return JSONResponse(
            content={"ready": False, "database": db},
            status_code=503,
        )
    return {"ready": True, "database": db}
