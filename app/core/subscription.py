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


async def subscription_block_detail(tenant: Tenant, db) -> dict:
    """Structured 403 payload for login and API — directs user to in-app ClickPesa checkout."""
    from app.services.clickpesa import clickpesa_configured

    past = days_past_expiry(tenant)
    g = grace_days()
    online = clickpesa_configured()

    if tenant.status == TenantStatus.suspended:
        title_en = "Account access is blocked"
        title_sw = "Huduma imezuiwa"
        if online:
            message_en = (
                f"{title_en}. Your shop subscription is not active. "
                "Open DukaDigital → Settings → Plan and pay with M-Pesa (USSD push on your phone)."
            )
            message_sw = (
                f"{title_sw}. Usajili wa duka hauko hai. "
                "Fungua DukaDigital → Mipangilio → Malipo na lipia kwa M-Pesa kwenye simu yako."
            )
        else:
            message_en = f"{title_en}. Contact DukaDigital support to renew your subscription."
            message_sw = f"{title_sw}. Wasiliana na msaada wa DukaDigital ili kufanya upya usajili."
    elif past is not None and past > g:
        if online:
            message_en = (
                "Your free trial or subscription has expired. "
                "Go to Settings → Plan and complete payment via M-Pesa to restore POS and reports."
            )
            message_sw = (
                "Jaribio la bure au usajili umeisha. "
                "Nenda Mipangilio → Malipo na kamilisha malipo kwa M-Pesa ili kufungua tena POS na ripoti."
            )
        else:
            message_en = "Your subscription has expired. Contact support to renew."
            message_sw = "Usajili umeisha. Wasiliana na msaada ili kufanya upya."
    else:
        message_en = subscription_status_message(tenant)
        message_sw = message_en

    return {
        "code": "subscription_required",
        "message": message_en,
        "message_sw": message_sw,
        "status": tenant.status.value,
        "payments_online": online,
        "payment_provider": "clickpesa",
    }


async def ensure_vendor_may_authenticate(user, tenant: Tenant | None, db) -> None:
    """Raise HTTP 403 when vendor/staff must not receive new tokens (unpaid / suspended)."""
    from fastapi import HTTPException, status
    from app.models import UserRole

    if user.role == UserRole.super_admin or tenant is None:
        return
    await sync_tenant_subscription_state(tenant, db)
    if subscription_allows_api_access(tenant):
        return
    # Persist auto-suspended / grace transitions before rejecting login.
    await db.commit()
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=await subscription_block_detail(tenant, db),
    )
