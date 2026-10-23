"""SaaS plans, subscription payments, and provider broadcasts."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, require_roles, require_tenant
from app.models import StaffRole
from app.services.platform_billing import get_billing_settings, get_grace_days
from app.database import get_db
from app.models import (
    PlatformBroadcast,
    PlatformPlan,
    SaaSPlanTier,
    StaffRole,
    SubscriptionPayment,
    Tenant,
    TenantStatus,
    User,
    UserRole,
)
from app.plan_catalog import DEFAULT_PLANS, SHARED_FEATURES, SHARED_FEATURES_SW
from app.seed_plans import seed_platform_plans

router = APIRouter(tags=["billing"])
logger = logging.getLogger(__name__)

_ONLINE_CHECKOUT_DISABLED = (
    "Online payment gateway is disabled. Renew via WhatsApp — open Settings → Plan & Billing in the app or website."
)


def _reject_online_checkout() -> None:
    raise HTTPException(status_code=503, detail=_ONLINE_CHECKOUT_DISABLED)


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
    tenant_id: str | None = None


class SubscriptionNudgeOut(BaseModel):
    show: bool
    days_left: int | None = None
    title_en: str = ""
    title_sw: str = ""
    message_en: str = ""
    message_sw: str = ""
    source: str = "none"
    subscription_expiry: str | None = None


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


def _require_subscription_payer(user: User) -> None:
    if user.role == UserRole.vendor_owner:
        return
    if user.role == UserRole.super_admin:
        raise HTTPException(status_code=403, detail="Super admin cannot pay tenant subscriptions here")
    if user.staff and user.staff.role in (StaffRole.owner, StaffRole.manager):
        return
    raise HTTPException(status_code=403, detail="Only shop owner or manager can pay for subscription")


class CheckoutPreviewIn(BaseModel):
    plan_tier: SaaSPlanTier | None = None
    billing_cycle: str = "monthly"
    phone_number: str


class CheckoutPushIn(BaseModel):
    plan_tier: SaaSPlanTier | None = None
    billing_cycle: str = "monthly"
    phone_number: str
    payment_channel: str | None = None


class CheckoutMethodsIn(BaseModel):
    plan_tier: SaaSPlanTier | None = None
    billing_cycle: str = "monthly"
    phone_number: str


class CheckoutMethodOut(BaseModel):
    name: str
    status: str
    fee: int | None = None


class CheckoutMethodsOut(BaseModel):
    amount_tzs: int
    active_methods: list[CheckoutMethodOut]
    detected_provider: str | None = None
    provider_message: str | None = None


class CheckoutOut(BaseModel):
    order_reference: str
    amount_tzs: int
    currency: str = "TZS"
    status: str
    clickpesa_id: str | None = None
    channel: str | None = None
    message_en: str = ""
    message_sw: str = ""
    active_methods: list[CheckoutMethodOut] = []
    payment_id: str


class CheckoutStatusOut(BaseModel):
    order_reference: str
    status: str
    provider_status: str | None = None
    subscription_expiry: str | None = None
    tenant_status: str | None = None
    message: str = ""


class TenantPaymentOut(BaseModel):
    id: str
    amount_tzs: float
    billing_cycle: str
    status: str
    payment_method: str
    order_reference: str | None
    created_at: str
    plan: str


async def _resolve_checkout_plan(db: AsyncSession, tenant: Tenant, tier: SaaSPlanTier | None) -> PlatformPlan:
    target = tier or tenant.plan
    result = await db.execute(select(PlatformPlan).where(PlatformPlan.tier == target))
    plan = result.scalar_one_or_none()
    if not plan:
        plans = await _load_plans(db)
        plan = next((p for p in plans if p.tier == target), None)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    if plan.contact_us:
        raise HTTPException(status_code=400, detail="Contact sales for this plan")
    return plan


async def _sync_checkout_from_clickpesa(
    db: AsyncSession, payment: SubscriptionPayment, tenant: Tenant
) -> SubscriptionPayment:
    from app.services.clickpesa import query_payment_by_order_reference
    from app.services.subscription_checkout import (
        SUCCESS_STATUSES,
        apply_subscription_payment_success,
        mark_subscription_payment_failed,
    )

    if not payment.order_reference or payment.status == "completed":
        return payment
    rows = await query_payment_by_order_reference(payment.order_reference)
    if not rows:
        return payment
    latest = rows[0]
    provider_status = str(latest.get("status") or "").upper()
    payment.provider_status = provider_status
    payment.clickpesa_id = str(latest.get("id") or payment.clickpesa_id or "")[:64] or payment.clickpesa_id
    if latest.get("paymentReference"):
        payment.reference = str(latest["paymentReference"])[:100]
    channel = str(latest.get("channel") or payment.provider_channel or "ClickPesa")
    if provider_status in SUCCESS_STATUSES:
        await apply_subscription_payment_success(
            db,
            payment,
            tenant=tenant,
            provider_reference=str(latest.get("paymentReference") or ""),
            channel=channel,
            provider_status=provider_status,
        )
    elif provider_status == "FAILED":
        await mark_subscription_payment_failed(
            db,
            payment,
            message=str(latest.get("message") or ""),
            provider_status=provider_status,
        )
    return payment


@router.post("/tenant/subscription/checkout/methods", response_model=CheckoutMethodsOut)
async def tenant_checkout_methods(
    body: CheckoutMethodsIn,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    _reject_online_checkout()
    from app.services.clickpesa import (
        ClickPesaError,
        clickpesa_configured,
        make_order_reference,
        merge_active_methods,
        normalize_payment_channel,
        phone_for_clickpesa,
        preview_ussd_push,
    )
    from app.services.subscription_checkout import resolve_checkout_amount

    if not clickpesa_configured():
        raise HTTPException(status_code=503, detail="Online payment is not configured yet")
    _require_subscription_payer(user)
    tenant_id = require_tenant(user)
    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    phone = phone_for_clickpesa(body.phone_number)
    if not phone:
        raise HTTPException(status_code=400, detail="Invalid Tanzania mobile number")

    cycle = "yearly" if body.billing_cycle == "yearly" else "monthly"
    plan = await _resolve_checkout_plan(db, tenant, body.plan_tier)
    amount = resolve_checkout_amount(plan, cycle)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Invalid plan amount")

    provider_message: str | None = None
    detected: str | None = None
    try:
        preview = await preview_ussd_push(
            amount_tzs=amount,
            order_reference=make_order_reference(),
            phone_number=phone,
            fetch_sender_details=True,
        )
        merged = merge_active_methods(
            preview.get("activeMethods") if isinstance(preview.get("activeMethods"), list) else None
        )
        sender = preview.get("sender") if isinstance(preview.get("sender"), dict) else {}
        detected = normalize_payment_channel(str(sender.get("accountProvider") or "")) if sender else None
    except ClickPesaError as exc:
        logger.warning("ClickPesa checkout methods unavailable: %s", exc)
        provider_message = str(exc)
        merged = merge_active_methods(None)
        for m in merged:
            m["status"] = "AVAILABLE"

    return CheckoutMethodsOut(
        amount_tzs=amount,
        active_methods=[
            CheckoutMethodOut(
                name=str(m.get("name") or ""),
                status=str(m.get("status") or ""),
                fee=int(m["fee"]) if m.get("fee") is not None else None,
            )
            for m in merged
        ],
        detected_provider=detected,
        provider_message=provider_message,
    )


@router.post("/tenant/subscription/checkout/preview", response_model=CheckoutOut)
async def tenant_checkout_preview(
    body: CheckoutPreviewIn,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    _reject_online_checkout()
    from app.services.clickpesa import (
        ClickPesaError,
        clickpesa_configured,
        make_order_reference,
        merge_active_methods,
        phone_for_clickpesa,
        preview_ussd_push,
    )
    from app.services.subscription_checkout import extend_months_for_cycle, resolve_checkout_amount

    if not clickpesa_configured():
        raise HTTPException(status_code=503, detail="Online payment is not configured yet")
    _require_subscription_payer(user)
    tenant_id = require_tenant(user)
    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    phone = phone_for_clickpesa(body.phone_number)
    if not phone:
        raise HTTPException(status_code=400, detail="Invalid Tanzania mobile number")

    cycle = "yearly" if body.billing_cycle == "yearly" else "monthly"
    plan = await _resolve_checkout_plan(db, tenant, body.plan_tier)
    amount = resolve_checkout_amount(plan, cycle)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Invalid plan amount")

    order_ref = make_order_reference()

    try:
        preview = await preview_ussd_push(
            amount_tzs=amount,
            order_reference=order_ref,
            phone_number=phone,
            fetch_sender_details=True,
        )
    except ClickPesaError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    merged = merge_active_methods(
        preview.get("activeMethods") if isinstance(preview.get("activeMethods"), list) else None
    )
    methods = [
        CheckoutMethodOut(
            name=str(m.get("name") or ""),
            status=str(m.get("status") or ""),
            fee=int(m["fee"]) if m.get("fee") is not None else None,
        )
        for m in merged
    ]

    payment = SubscriptionPayment(
        tenant_id=tenant.id,
        tenant_name=tenant.name,
        plan=plan.tier,
        amount_tzs=amount,
        payment_method="ClickPesa",
        order_reference=order_ref,
        phone_number=phone,
        billing_cycle=cycle,
        extend_months=extend_months_for_cycle(cycle),
        status="pending",
    )
    db.add(payment)
    await db.flush()

    return CheckoutOut(
        order_reference=order_ref,
        amount_tzs=amount,
        status="preview",
        active_methods=methods,
        payment_id=payment.id,
        message_en="Confirm your phone number, then tap Pay to receive the M-Pesa / mobile money prompt.",
        message_sw="Thibitisha namba ya simu, kisha bonyeza Lipa kupokea ombi la M-Pesa kwenye simu yako.",
    )


@router.post("/tenant/subscription/checkout/push", response_model=CheckoutOut)
async def tenant_checkout_push(
    body: CheckoutPushIn,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    _reject_online_checkout()
    from app.services.clickpesa import (
        CLICKPESA_PRE_KYC_MAX_TZS,
        ClickPesaError,
        clickpesa_checksum_configured,
        clickpesa_configured,
        initiate_ussd_push,
        make_order_reference,
        normalize_payment_channel,
        phone_for_clickpesa,
        preview_ussd_push,
        validate_payment_channel_choice,
    )
    from app.services.subscription_checkout import extend_months_for_cycle, resolve_checkout_amount

    if not clickpesa_configured():
        raise HTTPException(status_code=503, detail="Online payment is not configured yet")
    if not clickpesa_checksum_configured():
        raise HTTPException(
            status_code=503,
            detail="Payment security (checksum) is not configured on the server. Set CLICKPESA_CHECKSUM_SECRET on Railway.",
        )
    _require_subscription_payer(user)
    tenant_id = require_tenant(user)
    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    phone = phone_for_clickpesa(body.phone_number)
    if not phone:
        raise HTTPException(status_code=400, detail="Invalid Tanzania mobile number")

    cycle = "yearly" if body.billing_cycle == "yearly" else "monthly"
    plan = await _resolve_checkout_plan(db, tenant, body.plan_tier)
    amount = resolve_checkout_amount(plan, cycle)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Invalid plan amount")
    if amount > CLICKPESA_PRE_KYC_MAX_TZS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Plan amount TZS {amount:,} exceeds ClickPesa pre-KYC limit (TZS {CLICKPESA_PRE_KYC_MAX_TZS:,}). "
                "Complete ClickPesa KYC or choose Starter until approved."
            ),
        )

    try:
        preview = await preview_ussd_push(
            amount_tzs=amount,
            order_reference=make_order_reference(),
            phone_number=phone,
            fetch_sender_details=True,
        )
        sender = preview.get("sender") if isinstance(preview.get("sender"), dict) else {}
        detected = normalize_payment_channel(str(sender.get("accountProvider") or "")) if sender else None
        channel = validate_payment_channel_choice(
            body.payment_channel,
            preview.get("activeMethods") if isinstance(preview.get("activeMethods"), list) else None,
            detected,
        )
    except ClickPesaError as exc:
        code = 502 if exc.status_code is None or exc.status_code >= 500 else 400
        raise HTTPException(status_code=code, detail=str(exc)) from exc

    order_ref = make_order_reference()
    payment = SubscriptionPayment(
        tenant_id=tenant.id,
        tenant_name=tenant.name,
        plan=plan.tier,
        amount_tzs=amount,
        payment_method=channel,
        order_reference=order_ref,
        phone_number=phone,
        billing_cycle=cycle,
        extend_months=extend_months_for_cycle(cycle),
        status="processing",
        provider_channel=channel[:40],
    )
    db.add(payment)
    await db.flush()

    try:
        result = await initiate_ussd_push(
            amount_tzs=amount,
            order_reference=order_ref,
            phone_number=phone,
        )
    except ClickPesaError as exc:
        payment.status = "failed"
        payment.provider_message = str(exc)[:500]
        await db.flush()
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        payment.status = "failed"
        payment.provider_message = str(exc)[:500]
        await db.flush()
        raise HTTPException(status_code=502, detail="Could not start mobile payment. Try again.") from exc

    payment.clickpesa_id = str(result.get("id") or "")[:64] or None
    payment.provider_channel = str(result.get("channel") or channel)[:40] or None
    payment.provider_status = str(result.get("status") or "PROCESSING")
    await db.flush()

    return CheckoutOut(
        order_reference=order_ref,
        amount_tzs=amount,
        status=str(result.get("status") or "PROCESSING").lower(),
        clickpesa_id=payment.clickpesa_id,
        channel=payment.provider_channel or channel,
        payment_id=payment.id,
        message_en="Check your phone — approve the M-Pesa / mobile money request to complete payment.",
        message_sw="Angalia simu yako — kubali ombi la M-Pesa ili kukamilisha malipo.",
    )


@router.get("/tenant/subscription/checkout/{order_reference}", response_model=CheckoutStatusOut)
async def tenant_checkout_status(
    order_reference: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    from app.services.subscription_checkout import get_payment_by_order_reference

    _require_subscription_payer(user)
    tenant_id = require_tenant(user)
    payment = await get_payment_by_order_reference(db, order_reference[:24])
    if not payment or payment.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Payment not found")

    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    if payment.status != "completed":
        try:
            payment = await _sync_checkout_from_clickpesa(db, payment, tenant)
            await db.commit()
        except Exception:
            await db.rollback()

    expiry = (
        tenant.subscription_expiry.date().isoformat()
        if tenant.subscription_expiry
        else None
    )
    return CheckoutStatusOut(
        order_reference=payment.order_reference or order_reference,
        status=payment.status,
        provider_status=payment.provider_status,
        subscription_expiry=expiry,
        tenant_status=tenant.status.value,
        message=payment.provider_message or "",
    )


@router.get("/tenant/subscription/payments", response_model=list[TenantPaymentOut])
async def tenant_subscription_payments(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    _require_subscription_payer(user)
    tenant_id = require_tenant(user)
    result = await db.execute(
        select(SubscriptionPayment)
        .where(SubscriptionPayment.tenant_id == tenant_id)
        .order_by(SubscriptionPayment.created_at.desc())
        .limit(20)
    )
    out: list[TenantPaymentOut] = []
    for p in result.scalars().all():
        out.append(
            TenantPaymentOut(
                id=p.id,
                amount_tzs=p.amount_tzs,
                billing_cycle=p.billing_cycle,
                status=p.status,
                payment_method=p.payment_method,
                order_reference=p.order_reference,
                created_at=p.created_at.strftime("%Y-%m-%d %H:%M"),
                plan=p.plan.value,
            )
        )
    return out


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


def _days_until_expiry(expiry: datetime | None) -> int | None:
    if not expiry:
        return None
    today = datetime.now(UTC).date()
    exp_date = expiry.date() if isinstance(expiry, datetime) else expiry
    return (exp_date - today).days


@router.post("/admin/broadcasts", response_model=BroadcastOut, status_code=status.HTTP_201_CREATED)
async def send_broadcast(
    body: BroadcastCreate,
    user: Annotated[User, Depends(require_roles(UserRole.super_admin))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenants_result = await db.execute(select(Tenant))
    tenants = list(tenants_result.scalars().all())
    now = datetime.now(UTC)
    grace_days = await get_grace_days(db)

    if body.tenant_id:
        audience = [t for t in tenants if t.id == body.tenant_id]
        target_audience = f"tenant:{body.tenant_id}"
        target_region = audience[0].name if audience else body.tenant_id
    elif body.target == "unpaid":
        audience = []
        for t in tenants:
            days = _days_until_expiry(t.subscription_expiry)
            if t.status in (TenantStatus.grace_period, TenantStatus.suspended):
                audience.append(t)
            elif t.subscription_expiry and t.subscription_expiry < now:
                audience.append(t)
            elif days is not None and 0 <= days <= 3:
                audience.append(t)
        target_audience = "unpaid"
        target_region = f"{len(audience)} clients (expiring / unpaid)"
    else:
        audience = tenants
        target_audience = "all"
        target_region = "All clients"

    channel = body.channel if body.channel in ("in_app", "sms", "both") else "both"
    broadcast = PlatformBroadcast(
        title=body.title.strip(),
        message=body.message.strip(),
        target_audience=target_audience,
        target_region=target_region,
        channel=channel,
        sent_by=user.name,
        delivery_count=len(audience),
        status="sent",
    )
    db.add(broadcast)
    await db.flush()
    return _broadcast_out(broadcast)


@router.get("/tenant/subscription-nudge", response_model=SubscriptionNudgeOut)
async def tenant_subscription_nudge(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """In-app renewal nudge for shops expiring within 3 days or in grace."""
    if user.role == UserRole.super_admin:
        return SubscriptionNudgeOut(show=False)

    tenant_id = require_tenant(user)
    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        return SubscriptionNudgeOut(show=False)

    grace_days = await get_grace_days(db)
    days_left = _days_until_expiry(tenant.subscription_expiry)
    expiry_iso = (
        tenant.subscription_expiry.date().isoformat()
        if tenant.subscription_expiry
        else None
    )

    admin_row = (
        await db.execute(
            select(PlatformBroadcast)
            .where(PlatformBroadcast.target_audience == f"tenant:{tenant_id}")
            .order_by(PlatformBroadcast.sent_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    admin_fresh = (
        admin_row
        and admin_row.sent_at >= datetime.now(UTC) - timedelta(days=14)
        and admin_row.channel in ("in_app", "both")
    )

    in_grace = tenant.status == TenantStatus.grace_period
    expiring_soon = days_left is not None and 0 <= days_left <= 3
    past_grace_window = (
        days_left is not None
        and days_left < 0
        and grace_days > 0
        and abs(days_left) <= grace_days
    )
    show = bool(admin_fresh or in_grace or expiring_soon or past_grace_window)

    if not show:
        return SubscriptionNudgeOut(show=False, subscription_expiry=expiry_iso)

    shop = tenant.name or "Your shop"
    billing = await get_billing_settings(db)
    pay_hint_en = "Pay instantly in Settings → Plan with M-Pesa (USSD push on your phone)."
    pay_hint_sw = "Lipa papo hapo kwenye Mipangilio → Malipo kwa M-Pesa (ombi kwenye simu yako)."

    if admin_fresh and admin_row:
        return SubscriptionNudgeOut(
            show=True,
            days_left=days_left,
            title_en=admin_row.title,
            title_sw=admin_row.title,
            message_en=admin_row.message,
            message_sw=admin_row.message,
            source="admin",
            subscription_expiry=expiry_iso,
        )

    if in_grace or past_grace_window:
        title_en = "Grace period — renew now"
        title_sw = "Muda wa rehema — lipia haraka"
        message_en = f"{shop}: your subscription expired. {pay_hint_en}"
        message_sw = f"{shop}: usajili umeisha. {pay_hint_sw}"
        return SubscriptionNudgeOut(
            show=True,
            days_left=days_left,
            title_en=title_en,
            title_sw=title_sw,
            message_en=message_en,
            message_sw=message_sw,
            source="grace",
            subscription_expiry=expiry_iso,
        )

    day_label_en = "today" if days_left == 0 else f"in {days_left} day(s)"
    day_label_sw = "leo" if days_left == 0 else f"baada ya siku {days_left}"
    title_en = f"Plan ends {day_label_en}"
    title_sw = f"Usajili unaisha {day_label_sw}"
    message_en = f"{shop}: pay now to avoid interruption to POS, inventory, and reports. {pay_hint_en}"
    message_sw = f"{shop}: lipia sasa kuepuka kukatwa POS, stoo na ripoti. {pay_hint_sw}"
    return SubscriptionNudgeOut(
        show=True,
        days_left=days_left,
        title_en=title_en,
        title_sw=title_sw,
        message_en=message_en,
        message_sw=message_sw,
        source="auto",
        subscription_expiry=expiry_iso,
    )


# ── Platform billing settings (trial + Lipa / WhatsApp) ───────────────────────

class BillingSettingsOut(BaseModel):
    trial_days: int
    grace_days: int
    lipa_number: str
    lipa_name: str
    whatsapp_number: str
    support_note_en: str
    support_note_sw: str
    payments_online: bool = False
    payment_provider: str = "clickpesa"


class BillingSettingsUpdate(BaseModel):
    trial_days: int | None = None
    grace_days: int | None = None
    lipa_number: str | None = None
    lipa_name: str | None = None
    whatsapp_number: str | None = None
    support_note_en: str | None = None
    support_note_sw: str | None = None


def _billing_settings_out(raw: dict) -> BillingSettingsOut:
    from app.services.clickpesa import clickpesa_configured

    return BillingSettingsOut(
        **raw,
        payments_online=clickpesa_configured(),
        payment_provider="clickpesa",
    )


@router.get("/platform/billing-settings", response_model=BillingSettingsOut)
async def public_billing_settings(db: Annotated[AsyncSession, Depends(get_db)]):
    from app.services.platform_billing import get_billing_settings

    return _billing_settings_out(await get_billing_settings(db))


@router.get("/admin/billing-settings", response_model=BillingSettingsOut)
async def admin_get_billing_settings(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_roles(UserRole.super_admin))],
):
    from app.services.platform_billing import get_billing_settings

    return _billing_settings_out(await get_billing_settings(db))


@router.patch("/admin/billing-settings", response_model=BillingSettingsOut)
async def admin_patch_billing_settings(
    body: BillingSettingsUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_roles(UserRole.super_admin))],
):
    from app.services.platform_billing import update_billing_settings
    from app.core.subscription import set_grace_days_cache

    updated = await update_billing_settings(db, body.model_dump(exclude_unset=True))
    set_grace_days_cache(updated["grace_days"])
    await db.commit()
    return _billing_settings_out(updated)

