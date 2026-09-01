"""Provider portal sample data — subscription expiry, payments, broadcasts.

Runs after sample tenants exist. Idempotent via SEED-DEMO payment references.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import (
    PlatformBroadcast,
    PlatformPlan,
    SaaSPlanTier,
    SubscriptionPayment,
    Tenant,
    TenantStatus,
)
from app.seed_sample_data import SEED_MARKER

PAYMENT_REF_PREFIX = "SEED-DEMO"

async def _plan_prices(db) -> dict[SaaSPlanTier, float]:
    result = await db.execute(select(PlatformPlan))
    rows = {p.tier: float(p.price_monthly_tzs) for p in result.scalars().all()}
    defaults = {
        SaaSPlanTier.free_starter: 39000.0,
        SaaSPlanTier.biashara_pro: 79000.0,
        SaaSPlanTier.enterprise_chain: 250000.0,
    }
    for tier, amount in defaults.items():
        rows.setdefault(tier, amount)
    return rows


def _subscription_profile(idx: int, now: datetime) -> tuple[TenantStatus, datetime | None]:
    """Varied billing states for provider dashboard demos."""
    if idx % 5 == 4:
        return TenantStatus.pending_kyc, None
    if idx % 11 == 0:
        return TenantStatus.suspended, now - timedelta(days=21)
    if idx % 7 == 0:
        return TenantStatus.grace_period, now - timedelta(days=2)
    days_ahead = 12 + (idx % 6) * 14
    return TenantStatus.active, now + timedelta(days=days_ahead)


async def _provider_already_seeded(db) -> bool:
    count = await db.scalar(
        select(func.count(SubscriptionPayment.id)).where(
            SubscriptionPayment.reference.like(f"{PAYMENT_REF_PREFIX}-%")
        )
    )
    return (count or 0) > 0


async def seed_provider_data() -> None:
    """Backfill subscription billing data for sample tenants (idempotent)."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Tenant)
            .where(Tenant.owner_email.like(f"%{SEED_MARKER}"))
            .order_by(Tenant.created_at.asc())
        )
        tenants = list(result.scalars().all())
        if not tenants:
            return

        if await _provider_already_seeded(db):
            return

        now = datetime.now(UTC)
        prices = await _plan_prices(db)
        rng = random.Random(20240901)
        methods = ["M-Pesa", "M-Pesa", "Bank Transfer", "Tigo Pesa"]

        for idx, tenant in enumerate(tenants):
            status, expiry = _subscription_profile(idx, now)
            tenant.status = status
            tenant.subscription_expiry = expiry

            if tenant.plan == SaaSPlanTier.free_starter:
                payment_count = 1
            elif tenant.plan == SaaSPlanTier.biashara_pro:
                payment_count = rng.randint(2, 3)
            else:
                payment_count = rng.randint(2, 4)

            amount = prices.get(tenant.plan, 79000.0)
            if tenant.plan == SaaSPlanTier.enterprise_chain:
                amount = rng.choice([180000.0, 220000.0, 250000.0])

            for pidx in range(payment_count):
                paid_at = now - timedelta(days=30 * (payment_count - pidx) + rng.randint(0, 5))
                db.add(SubscriptionPayment(
                    tenant_id=tenant.id,
                    tenant_name=tenant.name,
                    plan=tenant.plan,
                    amount_tzs=amount,
                    payment_method=rng.choice(methods),
                    reference=f"{PAYMENT_REF_PREFIX}-{tenant.id[:8]}-{pidx + 1:02d}",
                    billing_cycle=rng.choice(["monthly", "monthly", "yearly"]),
                    status="completed",
                    created_at=paid_at,
                ))

        broadcasts = [
            PlatformBroadcast(
                title="Malipo ya Usajili — Kumbusho",
                message=(
                    "Habari! Usajili wako wa Duka+ unakaribia kuisha. "
                    "Tafadhali lipia kupitia M-Pesa au wasiliana na msaada wetu."
                ),
                target_audience="unpaid",
                target_region=f"{sum(1 for i, _ in enumerate(tenants) if i % 7 == 0 or i % 11 == 0)} clients",
                channel="both",
                sent_by=settings.super_admin_name,
                delivery_count=sum(1 for i, _ in enumerate(tenants) if i % 7 == 0 or i % 11 == 0),
                status="sent",
                sent_at=now - timedelta(days=3),
            ),
            PlatformBroadcast(
                title="TRA EFD Compliance Update",
                message=(
                    "Ensure your EFD serial is registered in Settings. "
                    "Duka+ receipts remain TRA-ready when your business profile is complete."
                ),
                target_audience="all",
                target_region="All clients",
                channel="in_app",
                sent_by=settings.super_admin_name,
                delivery_count=len(tenants),
                status="sent",
                sent_at=now - timedelta(days=14),
            ),
            PlatformBroadcast(
                title="Biashara Pro — Offer ya Septemba",
                message=(
                    "Pata mwezi wa ziada ukilipa mpango wa Biashara Pro kwa M-Pesa "
                    "kabla ya Septemba 30. Wasiliana na timu yetu kwa maelezo."
                ),
                target_audience="all",
                target_region="Dar es Salaam",
                channel="sms",
                sent_by=settings.super_admin_name,
                delivery_count=min(8, len(tenants)),
                status="sent",
                sent_at=now - timedelta(days=7),
            ),
        ]

        existing_broadcast = await db.scalar(
            select(func.count(PlatformBroadcast.id)).where(
                PlatformBroadcast.title == broadcasts[0].title
            )
        )
        if not existing_broadcast:
            for item in broadcasts:
                db.add(item)

        await db.commit()
