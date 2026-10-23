from contextlib import asynccontextmanager
from pathlib import Path
import logging
import os

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from app.api.v1 import (
    accounting,
    admin,
    ai,
    analytics,
    auth,
    billing,
    clickpesa_webhooks,
    business,
    demo,
    documents,
    extended,
    hr,
    payroll,
    platform,
    tenant,
    tenant_settings,
    tra_efd,
    workplace,
    pharmacy,
)
from app.config import settings
from app.database import engine, init_db
from app.startup_schema import run_startup_schema_patches
from app.health import check_database, get_system_status
from app.branch_backfill_migration import backfill_branch_ids
from app.payroll_profile_migration import migrate_payroll_contract_profile_column
from app.staff_permissions_migration import migrate_staff_permissions_payroll_profile
from app.staff_role_migration import migrate_staff_role_hr_enum
from app.branch_customer_migration import migrate_customer_branch_column
from app.branch_operational_migration import migrate_operational_branch_columns
from app.sale_refund_migration import migrate_sale_refund_columns
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
    await run_startup_schema_patches(engine)
    await migrate_plan_tier_enum()
    await migrate_customer_branch_column()
    await migrate_operational_branch_columns()
    await migrate_sale_refund_columns()
    await backfill_branch_ids()
    await migrate_payroll_contract_profile_column()
    await migrate_staff_permissions_payroll_profile()
    await migrate_staff_role_hr_enum()
    try:
        await seed_demo_data()
    except Exception:
        logger.exception("Startup seed failed")
        if settings.is_production:
            raise
    await seed_platform_plans()
    if settings.seed_demo_data:
        await seed_provider_data()
    await seed_platform_showcase()
    try:
        from app.database import AsyncSessionLocal
        from app.services.platform_billing import ensure_billing_settings, get_grace_days
        from app.core.subscription import set_grace_days_cache

        async with AsyncSessionLocal() as session:
            await ensure_billing_settings(session)
            set_grace_days_cache(await get_grace_days(session))
            await session.commit()
    except Exception:
        logger.exception("Billing settings seed failed")
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Duka+ SaaS ERP API — Production backend for Tanzania retail, pharmacy, and multi-business management",
    lifespan=lifespan,
)


@app.exception_handler(httpx.ConnectTimeout)
@app.exception_handler(httpx.ReadTimeout)
@app.exception_handler(httpx.NetworkError)
async def clickpesa_transport_exception_handler(_request: Request, exc: BaseException):
    logger.warning("Unhandled outbound HTTP error: %s", exc)
    detail = (
        "Payment provider is unreachable from the server (connection timed out). "
        "Verify CLICKPESA_API_BASE is https://api.clickpesa.com/third-parties and retry."
    )
    if isinstance(exc, httpx.ConnectTimeout):
        detail = (
            "Timed out connecting to ClickPesa from the server. "
            "Check Railway outbound HTTPS and CLICKPESA_* credentials, then redeploy."
        )
    return JSONResponse(status_code=502, content={"detail": detail})


try:
    from app.services.clickpesa import ClickPesaError

    @app.exception_handler(ClickPesaError)
    async def clickpesa_api_exception_handler(_request: Request, exc: ClickPesaError):
        from app.services.clickpesa import clickpesa_client_http_status

        return JSONResponse(
            status_code=clickpesa_client_http_status(exc.status_code),
            content={"detail": str(exc)},
        )
except ImportError:
    pass

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

app.include_router(auth.router, prefix="/api/v1")
app.include_router(business.router, prefix="/api/v1")
app.include_router(analytics.router, prefix="/api/v1")
app.include_router(tenant.router, prefix="/api/v1")
app.include_router(demo.router, prefix="/api/v1")
app.include_router(extended.router, prefix="/api/v1")
app.include_router(platform.router, prefix="/api/v1")
app.include_router(billing.router, prefix="/api/v1")
app.include_router(clickpesa_webhooks.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")
app.include_router(tenant_settings.router, prefix="/api/v1")
app.include_router(tra_efd.router, prefix="/api/v1")
app.include_router(accounting.router, prefix="/api/v1")
app.include_router(hr.router, prefix="/api/v1")
app.include_router(payroll.router, prefix="/api/v1")
app.include_router(documents.router, prefix="/api/v1")
app.include_router(workplace.router, prefix="/api/v1")
app.include_router(pharmacy.router, prefix="/api/v1")
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


@app.get("/health/clickpesa", include_in_schema=False)
@app.get("/api/health/clickpesa")
async def health_clickpesa():
    """Outbound connectivity to ClickPesa (does not expose secrets)."""
    from app.services.clickpesa import (
        ClickPesaError,
        _clickpesa_api_base,
        _get_bearer_token,
        _resolve_ipv4,
        clickpesa_configured,
        probe_clickpesa_tcp,
        railway_egress_hint,
    )

    base = _clickpesa_api_base()
    host = "api.clickpesa.com"
    ipv4 = await _resolve_ipv4(host)
    tcp = await probe_clickpesa_tcp(host=host)
    if ipv4 and not tcp.get("ok"):
        tcp_ipv4 = await probe_clickpesa_tcp(host=ipv4)
    else:
        tcp_ipv4 = None

    outbound_ip: str | None = None
    try:
        async with httpx.AsyncClient(timeout=8.0, trust_env=False) as client:
            ip_res = await client.get("https://api.ipify.org?format=json")
            if ip_res.status_code == 200:
                outbound_ip = str(ip_res.json().get("ip") or "") or None
    except Exception:
        outbound_ip = None

    report: dict = {
        "api_base": base,
        "resolved_ipv4": ipv4,
        "tcp": tcp,
        "tcp_ipv4_direct": tcp_ipv4,
        "outbound_ip": outbound_ip,
        "https_proxy_set": bool((settings.clickpesa_https_proxy or "").strip()),
        "trust_env": bool(settings.clickpesa_trust_env),
        "on_railway": bool(os.environ.get("RAILWAY_ENVIRONMENT", "").strip()),
        "railway_fix_hint": (
            "Disable Static Outbound IPs: Service → Settings → Networking → off → redeploy."
        ),
    }

    if not clickpesa_configured():
        return JSONResponse(
            status_code=503,
            content={
                **report,
                "status": "not_configured",
                "detail": "Set CLICKPESA_CLIENT_ID and CLICKPESA_API_KEY on Railway.",
            },
        )

    from app.services.clickpesa import clickpesa_in_cooldown

    if not tcp.get("ok"):
        return JSONResponse(
            status_code=502,
            content={
                **report,
                "status": "tcp_unreachable",
                "detail": (
                    "Cannot reach api.clickpesa.com:443 from this host."
                    f"{railway_egress_hint()}"
                ),
            },
        )

    if clickpesa_in_cooldown():
        return JSONResponse(
            status_code=502,
            content={
                **report,
                "status": "cooldown",
                "detail": "Recent ClickPesa failures; retry in ~45 seconds after fixing Railway networking.",
            },
        )

    try:
        await _get_bearer_token()
        return {**report, "status": "ok", "token": "ok"}
    except ClickPesaError as exc:
        detail = f"{exc}{railway_egress_hint()}".strip()
        status = "tcp_unreachable" if not tcp.get("ok") else "error"
        return JSONResponse(
            status_code=502,
            content={**report, "status": status, "detail": detail},
        )


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
