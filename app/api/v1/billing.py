"""SaaS plans, subscription payments, and provider broadcasts."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_roles
from app.database import get_db
from app.models import (
    PlatformBroadcast,
    PlatformPlan,
    SaaSPlanTier,
    SubscriptionPayment,
    Tenant,
    TenantStatus,
    User,
    UserRole,
)
from app.plan_catalog import DEFAULT_PLANS, SHARED_FEATURES, SHARED_FEATURES_SW
from app.seed_plans import seed_platform_plans

router = APIRouter(tags=["billing"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class PlanOut(BaseModel):
    id: str
    tier: str
    name: str
    name_sw: str
    tag_en: str
    tag_sw: str
    price_monthly_tzs: float
    price_yearly_tzs: float
    max_branches: int
    max_staff: int
    max_products: int
    features: list[str]
    features_sw: list[str]
    contact_us: bool
    popular: bool
    sort_order: int
    active_subscribers_count: int = 0

    model_config = {"from_attributes": True}


class PlanFeaturesSync(BaseModel):
    features: list[str]
    features_sw: list[str]


class PlanUpdate(BaseModel):
    name: str | None = None
    name_sw: str | None = None
    tag_en: str | None = None
    tag_sw: str | None = None
    price_monthly_tzs: float | None = None
    price_yearly_tzs: float | None = None
    max_branches: int | None = None
    max_staff: int | None = None
    max_products: int | None = None
    features: list[str] | None = None
    features_sw: list[str] | None = None
    contact_us: bool | None = None
    popular: bool | None = None


class PaymentOut(BaseModel):
    id: str
    store_id: str
    store_name: str
    plan: str
    amount_tzs: float
    payment_method: str
    reference: str
    date: str
    status: str
    billing_cycle: str

    model_config = {"from_attributes": True}


class PaymentCreate(BaseModel):
    store_id: str
    amount_tzs: float | None = None
    payment_method: str = "M-Pesa"
    reference: str = ""
    billing_cycle: str = "monthly"
    extend_months: int = 1


class BroadcastOut(BaseModel):
    id: str
    title: str
    message: str
    target_audience: str
    target_region: str
    channel: str
    sent_at: str
    sent_by: str
    delivery_count: int
    status: str

    model_config = {"from_attributes": True}


class BroadcastCreate(BaseModel):
    title: str
    message: str
    channel: str = "both"
    target: str = "unpaid"  # all | unpaid


def _plan_out(p: PlatformPlan, subscribers: int = 0) -> PlanOut:
    return PlanOut(
        id=p.id,
        tier=p.tier.value,
        name=p.name,
        name_sw=p.name_sw,
        tag_en=p.tag_en,
        tag_sw=p.tag_sw,
        price_monthly_tzs=p.price_monthly_tzs,
        price_yearly_tzs=p.price_yearly_tzs,
        max_branches=p.max_branches,
        max_staff=p.max_staff,
        max_products=p.max_products,
        features=p.features or [],
        features_sw=p.features_sw or [],
        contact_us=p.contact_us,
        popular=p.popular,
        sort_order=p.sort_order,
        active_subscribers_count=subscribers,
    )


def _payment_out(p: SubscriptionPayment) -> PaymentOut:
    return PaymentOut(
        id=p.id,
        store_id=p.tenant_id,
        store_name=p.tenant_name,
        plan=p.plan.value,
        amount_tzs=p.amount_tzs,
        payment_method=p.payment_method,
        reference=p.reference,
        date=p.created_at.strftime("%Y-%m-%d %H:%M"),
        status=p.status,
        billing_cycle=p.billing_cycle,
    )


def _broadcast_out(b: PlatformBroadcast) -> BroadcastOut:
    return BroadcastOut(
        id=b.id,
        title=b.title,
        message=b.message,
        target_audience=b.target_audience,
        target_region=b.target_region,
        channel=b.channel,
        sent_at=b.sent_at.strftime("%Y-%m-%d %H:%M"),
        sent_by=b.sent_by,
        delivery_count=b.delivery_count,
        status=b.status,
    )


async def _load_plans(db: AsyncSession) -> list[PlatformPlan]:
    result = await db.execute(
        select(PlatformPlan)
        .where(PlatformPlan.is_active.is_(True))
        .order_by(PlatformPlan.sort_order.asc())
    )
    plans = list(result.scalars().all())
    if not plans:
        await seed_platform_plans()
        result = await db.execute(
            select(PlatformPlan)
            .where(PlatformPlan.is_active.is_(True))
            .order_by(PlatformPlan.sort_order.asc())
        )
        plans = list(result.scalars().all())
    return plans


# ── Public plans (landing page) ───────────────────────────────────────────────

@router.get("/platform/plans", response_model=list[PlanOut])
async def public_list_plans(db: Annotated[AsyncSession, Depends(get_db)]):
    plans = await _load_plans(db)
    return [_plan_out(p) for p in plans]


# ── Admin plans ───────────────────────────────────────────────────────────────

@router.get("/admin/plans", response_model=list[PlanOut])
async def admin_list_plans(
    user: Annotated[User, Depends(require_roles(UserRole.super_admin))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    plans = await _load_plans(db)
    out: list[PlanOut] = []
    from sqlalchemy import func
    for p in plans:
        subs = int(await db.scalar(
            select(func.count(Tenant.id)).where(Tenant.plan == p.tier, Tenant.status == TenantStatus.active)
        ) or 0)
        out.append(_plan_out(p, subs))
    return out


async def _sync_shared_features(db: AsyncSession, features: list[str], features_sw: list[str]) -> None:
    result = await db.execute(select(PlatformPlan))
    for row in result.scalars().all():
        row.features = features
        row.features_sw = features_sw


@router.patch("/admin/plans/{plan_id}", response_model=PlanOut)
async def admin_update_plan(
    plan_id: str,
    body: PlanUpdate,
    user: Annotated[User, Depends(require_roles(UserRole.super_admin))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(select(PlatformPlan).where(PlatformPlan.id == plan_id))
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    updates = body.model_dump(exclude_unset=True)
    if updates.get("popular"):
        others = await db.execute(select(PlatformPlan).where(PlatformPlan.id != plan_id))
        for row in others.scalars().all():
            row.popular = False
    for k, v in updates.items():
        setattr(plan, k, v)
    if "features" in updates or "features_sw" in updates:
        await _sync_shared_features(
            db,
            updates.get("features") or plan.features or list(SHARED_FEATURES),
            updates.get("features_sw") or plan.features_sw or list(SHARED_FEATURES_SW),
        )
    await db.flush()
    plans = await _load_plans(db)
    updated = next((p for p in plans if p.id == plan_id), plan)
    return _plan_out(updated)


@router.put("/admin/plans/shared-features", response_model=list[PlanOut])
async def admin_sync_shared_features(
    body: PlanFeaturesSync,
    user: Annotated[User, Depends(require_roles(UserRole.super_admin))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await _sync_shared_features(db, body.features, body.features_sw)
    await db.flush()
    plans = await _load_plans(db)
    return [_plan_out(p) for p in plans]


@router.post("/admin/plans/reset", response_model=list[PlanOut])
async def admin_reset_plans(
    user: Annotated[User, Depends(require_roles(UserRole.super_admin))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    existing = await db.execute(select(PlatformPlan))
    for row in existing.scalars().all():
        await db.delete(row)
    await db.flush()
    for spec in DEFAULT_PLANS:
        db.add(PlatformPlan(**spec))
    await db.flush()
    plans = await _load_plans(db)
    return [_plan_out(p) for p in plans]


# ── Subscription payments ─────────────────────────────────────────────────────

@router.get("/admin/subscription-payments", response_model=list[PaymentOut])
async def list_payments(
    user: Annotated[User, Depends(require_roles(UserRole.super_admin))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(
        select(SubscriptionPayment).order_by(SubscriptionPayment.created_at.desc())
    )
    return [_payment_out(p) for p in result.scalars().all()]


@router.post("/admin/subscription-payments", response_model=PaymentOut, status_code=status.HTTP_201_CREATED)
async def record_payment(
    body: PaymentCreate,
    user: Annotated[User, Depends(require_roles(UserRole.super_admin))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(select(Tenant).where(Tenant.id == body.store_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    plan_row = await db.execute(select(PlatformPlan).where(PlatformPlan.tier == tenant.plan))
    plan = plan_row.scalar_one_or_none()
    amount = body.amount_tzs if body.amount_tzs is not None else (plan.price_monthly_tzs if plan else 0)

    payment = SubscriptionPayment(
        tenant_id=tenant.id,
        tenant_name=tenant.name,
        plan=tenant.plan,
        amount_tzs=amount,
        payment_method=body.payment_method,
        reference=body.reference or f"MPESA-{int(datetime.now(UTC).timestamp())}",
        billing_cycle=body.billing_cycle,
        status="completed",
    )
    db.add(payment)

    now = datetime.now(UTC)
    base = tenant.subscription_expiry if tenant.subscription_expiry and tenant.subscription_expiry > now else now
    tenant.subscription_expiry = base + timedelta(days=30 * body.extend_months)
    tenant.status = TenantStatus.active
    await db.flush()
    return _payment_out(payment)


# ── Broadcasts / reminders ────────────────────────────────────────────────────

@router.get("/admin/broadcasts", response_model=list[BroadcastOut])
async def list_broadcasts(
    user: Annotated[User, Depends(require_roles(UserRole.super_admin))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(
        select(PlatformBroadcast).order_by(PlatformBroadcast.sent_at.desc())
    )
    return [_broadcast_out(b) for b in result.scalars().all()]


@router.post("/admin/broadcasts", response_model=BroadcastOut, status_code=status.HTTP_201_CREATED)
async def send_broadcast(
    body: BroadcastCreate,
    user: Annotated[User, Depends(require_roles(UserRole.super_admin))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenants_result = await db.execute(select(Tenant))
    tenants = list(tenants_result.scalars().all())
    now = datetime.now(UTC)

    if body.target == "unpaid":
        audience = [
            t for t in tenants
            if t.status in (TenantStatus.grace_period, TenantStatus.suspended)
            or (t.subscription_expiry and t.subscription_expiry < now)
        ]
    else:
        audience = tenants

    channel = body.channel if body.channel in ("in_app", "sms", "both") else "both"
    broadcast = PlatformBroadcast(
        title=body.title.strip(),
        message=body.message.strip(),
        target_audience="all",
        target_region=f"{len(audience)} clients" if body.target == "unpaid" else "All clients",
        channel=channel,
        sent_by=user.name,
        delivery_count=len(audience),
        status="sent",
    )
    db.add(broadcast)
    await db.flush()
    return _broadcast_out(broadcast)
