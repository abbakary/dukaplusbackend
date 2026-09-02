"""Branch provisioning — create sub-branches with isolated admin accounts."""

from datetime import UTC, datetime

from fastapi import HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import DEFAULT_PERMISSIONS, get_user_by_email, hash_password
from app.models import Branch, PlatformPlan, StaffMember, StaffRole, Tenant, User, UserRole


class BranchAdminCreate(BaseModel):
    name: str
    email: EmailStr
    phone: str = ""
    password: str = Field(min_length=6)


class BranchCreate(BaseModel):
    name: str
    code: str | None = None
    branch_type: str = "sub_branch"
    region: str = "Dar es Salaam"
    district: str = ""
    address: str = ""
    phone: str = ""
    tra_efd_serial: str = ""
    opening_hours: str = "08:00 - 20:00"
    admin: BranchAdminCreate


async def get_plan_max_branches(db: AsyncSession, tenant: Tenant) -> int:
    result = await db.execute(select(PlatformPlan).where(PlatformPlan.tier == tenant.plan))
    plan = result.scalar_one_or_none()
    return plan.max_branches if plan else 1


async def _next_branch_code(db: AsyncSession, tenant_id: str, region: str) -> str:
    count = await db.scalar(select(func.count(Branch.id)).where(Branch.tenant_id == tenant_id)) or 0
    prefix = (region or "BR")[:3].upper().replace(" ", "")
    return f"BR-{prefix}-{count + 1:02d}"


async def create_branch_with_admin(
    db: AsyncSession,
    tenant: Tenant,
    body: BranchCreate,
) -> tuple[Branch, StaffMember]:
    branch_count = await db.scalar(
        select(func.count(Branch.id)).where(Branch.tenant_id == tenant.id)
    ) or 0
    max_branches = await get_plan_max_branches(db, tenant)
    if branch_count >= max_branches:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Your plan allows up to {max_branches} branch(es). Upgrade to add more.",
        )

    if body.branch_type == "main_hq":
        existing_hq = await db.execute(
            select(Branch).where(Branch.tenant_id == tenant.id, Branch.branch_type == "main_hq")
        )
        if existing_hq.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Headquarters branch already exists")

    code = (body.code or "").strip() or await _next_branch_code(db, tenant.id, body.region)
    dup = await db.execute(
        select(Branch).where(Branch.tenant_id == tenant.id, Branch.code == code)
    )
    if dup.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"Branch code '{code}' already in use")

    admin_email = body.admin.email.strip().lower()
    existing_user = await get_user_by_email(db, admin_email)
    if existing_user:
        raise HTTPException(status_code=400, detail="Branch admin email already registered")

    branch = Branch(
        tenant_id=tenant.id,
        name=body.name.strip(),
        code=code,
        branch_type=body.branch_type,
        region=body.region,
        district=body.district,
        address=body.address,
        phone=body.phone,
        tra_efd_serial=body.tra_efd_serial,
        opening_hours=body.opening_hours,
        status="active",
    )
    db.add(branch)
    await db.flush()

    staff = StaffMember(
        tenant_id=tenant.id,
        branch_id=branch.id,
        name=body.admin.name.strip(),
        email=admin_email,
        phone=body.admin.phone,
        role=StaffRole.manager,
        permissions=DEFAULT_PERMISSIONS.get("Manager", DEFAULT_PERMISSIONS["Cashier"]),
    )
    db.add(staff)
    await db.flush()

    db.add(
        User(
            email=admin_email,
            hashed_password=hash_password(body.admin.password),
            name=body.admin.name.strip(),
            phone=body.admin.phone,
            role=UserRole.vendor_staff,
            tenant_id=tenant.id,
            staff_id=staff.id,
        )
    )
    await db.flush()
    return branch, staff


async def enrich_branch_row(db: AsyncSession, branch: Branch) -> dict:
    """Attach manager + staff count for API responses."""
    mgr = await db.execute(
        select(StaffMember)
        .where(
            StaffMember.branch_id == branch.id,
            StaffMember.active == True,  # noqa: E712
            StaffMember.role == StaffRole.manager,
        )
        .order_by(StaffMember.joined_date)
        .limit(1)
    )
    manager = mgr.scalar_one_or_none()
    staff_count = await db.scalar(
        select(func.count(StaffMember.id)).where(
            StaffMember.branch_id == branch.id,
            StaffMember.active == True,  # noqa: E712
        )
    ) or 0
    created = branch.created_at
    if created and created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return {
        "id": branch.id,
        "name": branch.name,
        "code": branch.code,
        "branch_type": branch.branch_type,
        "status": branch.status,
        "region": branch.region,
        "district": branch.district,
        "address": branch.address,
        "phone": branch.phone,
        "tra_efd_serial": branch.tra_efd_serial,
        "opening_hours": branch.opening_hours,
        "manager_staff_id": manager.id if manager else None,
        "manager_name": manager.name if manager else None,
        "staff_count": int(staff_count),
        "created_at": created.isoformat() if created else None,
    }
