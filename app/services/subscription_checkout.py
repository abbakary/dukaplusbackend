"""Apply ClickPesa subscription payments to tenant billing state."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SaaSPlanTier, SubscriptionPayment, Tenant, TenantStatus


SUCCESS_STATUSES = frozenset({"SUCCESS", "SETTLED"})


async def get_payment_by_order_reference(
    db: AsyncSession, order_reference: str
) -> SubscriptionPayment | None:
    result = await db.execute(
        select(SubscriptionPayment).where(SubscriptionPayment.order_reference == order_reference)
    )
    return result.scalar_one_or_none()


async def apply_subscription_payment_success(
    db: AsyncSession,
    payment: SubscriptionPayment,
    *,
    tenant: Tenant,
    provider_reference: str = "",
    channel: str = "ClickPesa",
    provider_status: str = "SUCCESS",
) -> SubscriptionPayment:
    if payment.status == "completed":
        return payment

    payment.status = "completed"
    payment.payment_method = channel or "ClickPesa"
    payment.provider_status = provider_status
    if provider_reference:
        payment.provider_reference = provider_reference[:100]

    months = payment.extend_months or (12 if payment.billing_cycle == "yearly" else 1)
    now = datetime.now(UTC)
    base = (
        tenant.subscription_expiry
        if tenant.subscription_expiry and tenant.subscription_expiry > now
        else now
    )
    tenant.subscription_expiry = base + timedelta(days=30 * months)
    tenant.status = TenantStatus.active
    if payment.plan:
        tenant.plan = payment.plan

    await db.flush()
    return payment


async def mark_subscription_payment_failed(
    db: AsyncSession,
    payment: SubscriptionPayment,
    *,
    message: str = "",
    provider_status: str = "FAILED",
) -> None:
    if payment.status == "completed":
        return
    payment.status = "failed"
    payment.provider_status = provider_status
    if message:
        payment.provider_message = message[:500]
    await db.flush()


def resolve_checkout_amount(plan_row, billing_cycle: str) -> int:
    if billing_cycle == "yearly":
        return int(plan_row.price_yearly_tzs or 0)
    return int(plan_row.price_monthly_tzs or 0)


def extend_months_for_cycle(billing_cycle: str) -> int:
    return 12 if billing_cycle == "yearly" else 1
