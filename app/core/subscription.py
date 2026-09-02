"""Tenant subscription state — grace period, suspension, API access."""

from datetime import UTC, datetime, timedelta

from app.models import Tenant, TenantStatus

GRACE_DAYS = 7
DEMO_TENANT_EMAIL_MARKER = "sample.dukaplus.co.tz"


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

    now = datetime.now(UTC)

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

    if past <= GRACE_DAYS:
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
    if past is not None and past > GRACE_DAYS:
        return False
    return True


def subscription_status_message(tenant: Tenant) -> str:
    if tenant.status == TenantStatus.suspended:
        return "Account suspended — renew subscription to restore access."
    past = days_past_expiry(tenant)
    if past is not None and past > GRACE_DAYS:
        return "Subscription expired — payment required."
    if tenant.status == TenantStatus.grace_period:
        return f"Grace period — {max(0, GRACE_DAYS - past)} day(s) remaining."
    return "Active"
