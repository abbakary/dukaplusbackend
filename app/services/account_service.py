"""Shared tenant + owner account provisioning."""

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_user_by_email, hash_password
from app.models import (
    Branch,
    BusinessType,
    SaaSPlanTier,
    StaffMember,
    StaffRole,
    Tenant,
    TenantStatus,
    User,
    UserRole,
)
from app.schemas import RegisterRequest


async def create_tenant_with_owner(
    db: AsyncSession,
    body: RegisterRequest,
    *,
    tenant_status: TenantStatus = TenantStatus.active,
    plan: SaaSPlanTier = SaaSPlanTier.free_starter,
) -> tuple[Tenant, User]:
    existing = await get_user_by_email(db, body.email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    try:
        biz_type = BusinessType(body.business_type)
    except ValueError:
        biz_type = BusinessType.retail

    tenant = Tenant(
        name=body.business_name,
        owner_name=body.owner_name,
        owner_email=body.email,
        owner_phone=body.phone,
        business_type=biz_type,
        region=body.region,
        district=body.district,
        tin_number=body.tin_number,
        license_number=body.license_number,
        plan=plan,
        status=tenant_status,
    )
    db.add(tenant)
    await db.flush()

    branch = Branch(
        tenant_id=tenant.id,
        name=f"{body.business_name} - HQ",
        code="HQ01",
        branch_type="main_hq",
        region=body.region,
        district=body.district,
    )
    db.add(branch)
    await db.flush()

    staff = StaffMember(
        tenant_id=tenant.id,
        branch_id=branch.id,
        name=body.owner_name,
        email=body.email,
        phone=body.phone,
        role=StaffRole.owner,
        permissions={
            "canSellPOS": True,
            "canGiveCredit": True,
            "canModifyInventory": True,
            "canViewProfitReports": True,
            "canManageSuppliers": True,
            "canApproveDiscounts": True,
            "canVoidReceipts": True,
            "canPerformDailyClosing": True,
            "canAccessSuperAdmin": False,
        },
    )
    db.add(staff)
    await db.flush()

    user = User(
        email=body.email,
        hashed_password=hash_password(body.password),
        name=body.owner_name,
        phone=body.phone,
        role=UserRole.vendor_owner,
        tenant_id=tenant.id,
        staff_id=staff.id,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user, ["tenant", "staff"])

    return tenant, user
