"""Tenant subscription state — grace period, suspension, API access."""

from datetime import UTC, datetime, timedelta

from app.models import Tenant, TenantStatus

# Default when platform settings unavailable; provider can override via billing settings.
DEFAULT_GRACE_DAYS = 0
DEMO_TENANT_EMAIL_MARKER = "sample.dukaplus.co.tz"

# Mutable cache refreshed from DB on sync (avoids async in every check)
_GRACE_DAYS_CACHE = DEFAULT_GRACE_DAYS


def set_grace_days_cache(days: int) -> None:
    global _GRACE_DAYS_CACHE
    _GRACE_DAYS_CACHE = max(0, int(days))


def grace_days() -> int:
    return _GRACE_DAYS_CACHE


def _is_demo_tenant(tenant: Tenant) -> bool:
    email = (tenant.owner_email or "").lower()
    return DEMO_TENANT_EMAIL_MARKER in email


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def days_past_expiry(tenant: Tenant, now: datetime | None = None) -> int | None:
    exp = _aware(tenant.subscription_expiry)
    if not exp:
        return None
    ref = now or datetime.now(UTC)
    if exp >= ref:
        return 0
    return (ref - exp).days


async def sync_tenant_subscription_state(tenant: Tenant, db) -> None:
    """Auto-transition active → grace → suspended based on expiry."""
    if tenant.status == TenantStatus.pending_kyc:
        return

    try:
        from app.services.platform_billing import get_grace_days

        set_grace_days_cache(await get_grace_days(db))
    except Exception:
        pass

    now = datetime.now(UTC)
    grace = grace_days()

    # Demo/sample tenants stay active for trials and Vercel demos
    if _is_demo_tenant(tenant):
        exp = _aware(tenant.subscription_expiry)
        if exp is None or exp < now + timedelta(days=7):
            tenant.subscription_expiry = now + timedelta(days=30)
        if tenant.status in (TenantStatus.suspended, TenantStatus.grace_period):
            tenant.status = TenantStatus.active
        await db.flush()
        return

    past = days_past_expiry(tenant, now)

    if tenant.status == TenantStatus.suspended:
        return

    if past is None or past == 0:
        if tenant.status == TenantStatus.grace_period:
            tenant.status = TenantStatus.active
        return

    if grace > 0 and past <= grace:
        if tenant.status == TenantStatus.active:
            tenant.status = TenantStatus.grace_period
        return

    tenant.status = TenantStatus.suspended
    await db.flush()


def subscription_allows_api_access(tenant: Tenant) -> bool:
    """Vendor API access — blocked when suspended or overdue past grace."""
    if tenant.status == TenantStatus.suspended:
        return False
    if tenant.status == TenantStatus.pending_kyc:
        return True
    past = days_past_expiry(tenant)
    if past is not None and past > grace_days():
        return False
    return True


def subscription_status_message(tenant: Tenant) -> str:
    if tenant.status == TenantStatus.suspended:
        return "Account suspended — renew subscription to restore access. Contact WhatsApp for payment help."
    past = days_past_expiry(tenant)
    g = grace_days()
    if past is not None and past > g:
        return "Free trial / subscription expired — upgrade to a paid package to continue."
    if tenant.status == TenantStatus.grace_period and past is not None:
        return f"Grace period — {max(0, g - past)} day(s) remaining. Please upgrade."
    return "Active"
