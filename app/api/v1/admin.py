"""Super-admin platform management APIs."""

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import require_roles
from app.core.security import get_user_by_email, hash_password
from app.database import get_db
from app.models import Customer, PlatformShowcaseItem, Product, SaaSPlanTier, Sale, Tenant, TenantStatus, User, UserRole
from app.schemas import RegisterRequest
from app.services.account_service import create_tenant_with_owner

router = APIRouter(prefix="/admin", tags=["admin"])


class TenantAdminOut(BaseModel):
    id: str
    name: str
    owner_name: str
    owner_email: str
    owner_phone: str
    business_type: str
    region: str
    district: str
    plan: str
    status: str
    tra_efd_serial: str
    created_at: datetime
    branches_count: int = 0
    products_count: int = 0
    customers_count: int = 0
    monthly_revenue: float = 0

    model_config = {"from_attributes": True}


class TenantStatusUpdate(BaseModel):
    status: str | None = None
    plan: str | None = None


class AdminCreateTenantRequest(BaseModel):
    business_name: str
    owner_name: str
    email: str
    phone: str
    password: str
    business_type: str = "retail"
    tin_number: str = ""
    license_number: str = ""
    region: str = "Dar es Salaam"
    district: str = ""
    plan: str = "free_starter"
    status: str = "active"


class AdminCreateSuperAdminRequest(BaseModel):
    name: str
    email: str
    phone: str = ""
    password: str


class PlatformMetrics(BaseModel):
    total_tenants: int
    active_tenants: int
    pending_kyc: int
    suspended_tenants: int
    total_revenue_month: float
    total_sales_month: int
    tenants_by_type: dict[str, int]


@router.get("/metrics", response_model=PlatformMetrics)
async def platform_metrics(
    user: Annotated[User, Depends(require_roles(UserRole.super_admin))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    total = await db.scalar(select(func.count(Tenant.id))) or 0
    active = await db.scalar(
        select(func.count(Tenant.id)).where(Tenant.status == TenantStatus.active)
    ) or 0
    pending = await db.scalar(
        select(func.count(Tenant.id)).where(Tenant.status == TenantStatus.pending_kyc)
    ) or 0
    suspended = await db.scalar(
        select(func.count(Tenant.id)).where(Tenant.status == TenantStatus.suspended)
    ) or 0

    month_start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    rev = await db.scalar(
        select(func.coalesce(func.sum(Sale.total), 0)).where(Sale.created_at >= month_start)
    ) or 0
    sales_count = await db.scalar(
        select(func.count(Sale.id)).where(Sale.created_at >= month_start)
    ) or 0

    type_rows = await db.execute(
        select(Tenant.business_type, func.count(Tenant.id)).group_by(Tenant.business_type)
    )
    by_type = {row[0].value: row[1] for row in type_rows.all()}

    return PlatformMetrics(
        total_tenants=int(total),
        active_tenants=int(active),
        pending_kyc=int(pending),
        suspended_tenants=int(suspended),
        total_revenue_month=float(rev),
        total_sales_month=int(sales_count),
        tenants_by_type=by_type,
    )


@router.get("/tenants", response_model=list[TenantAdminOut])
async def list_tenants(
    user: Annotated[User, Depends(require_roles(UserRole.super_admin))],
    db: Annotated[AsyncSession, Depends(get_db)],
    status_filter: str | None = None,
    business_type: str | None = None,
):
    q = select(Tenant).options(selectinload(Tenant.branches)).order_by(Tenant.created_at.desc())
    if status_filter:
        q = q.where(Tenant.status == status_filter)
    if business_type:
        q = q.where(Tenant.business_type == business_type)
    result = await db.execute(q)
    tenants = result.scalars().all()

    month_start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    out: list[TenantAdminOut] = []
    for t in tenants:
        pc = await db.scalar(select(func.count(Product.id)).where(Product.tenant_id == t.id)) or 0
        cc = await db.scalar(select(func.count(Customer.id)).where(Customer.tenant_id == t.id)) or 0
        mr = await db.scalar(
            select(func.coalesce(func.sum(Sale.total), 0)).where(
                Sale.tenant_id == t.id, Sale.created_at >= month_start
            )
        ) or 0
        out.append(TenantAdminOut(
            id=t.id,
            name=t.name,
            owner_name=t.owner_name,
            owner_email=t.owner_email,
            owner_phone=t.owner_phone,
            business_type=t.business_type.value,
            region=t.region,
            district=t.district,
            plan=t.plan.value,
            status=t.status.value,
            tra_efd_serial=t.tra_efd_serial or "",
            created_at=t.created_at,
            branches_count=len(t.branches),
            products_count=int(pc),
            customers_count=int(cc),
            monthly_revenue=float(mr),
        ))
    return out


@router.post("/tenants", response_model=TenantAdminOut, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    body: AdminCreateTenantRequest,
    user: Annotated[User, Depends(require_roles(UserRole.super_admin))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    try:
        tenant_status = TenantStatus(body.status)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid status: {body.status}") from e
    try:
        plan = SaaSPlanTier(body.plan)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid plan: {body.plan}") from e

    register_body = RegisterRequest(
        business_name=body.business_name,
        owner_name=body.owner_name,
        email=body.email,
        phone=body.phone,
        password=body.password,
        business_type=body.business_type,
        tin_number=body.tin_number,
        license_number=body.license_number,
        region=body.region,
        district=body.district,
    )
    tenant, _ = await create_tenant_with_owner(
        db,
        register_body,
        tenant_status=tenant_status,
        plan=plan,
    )
    return await get_tenant(tenant.id, user, db)


@router.post("/users/super-admin", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_super_admin(
    body: AdminCreateSuperAdminRequest,
    user: Annotated[User, Depends(require_roles(UserRole.super_admin))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    existing = await get_user_by_email(db, body.email)
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    new_admin = User(
        email=body.email,
        hashed_password=hash_password(body.password),
        name=body.name,
        phone=body.phone,
        role=UserRole.super_admin,
    )
    db.add(new_admin)
    await db.flush()
    return {
        "id": new_admin.id,
        "email": new_admin.email,
        "name": new_admin.name,
        "role": new_admin.role.value,
        "message": "Super admin account created",
    }


@router.get("/tenants/{tenant_id}", response_model=TenantAdminOut)
async def get_tenant(
    tenant_id: str,
    user: Annotated[User, Depends(require_roles(UserRole.super_admin))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(
        select(Tenant).options(selectinload(Tenant.branches)).where(Tenant.id == tenant_id)
    )
    t = result.scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail="Tenant not found")
    month_start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    pc = await db.scalar(select(func.count(Product.id)).where(Product.tenant_id == t.id)) or 0
    cc = await db.scalar(select(func.count(Customer.id)).where(Customer.tenant_id == t.id)) or 0
    mr = await db.scalar(
        select(func.coalesce(func.sum(Sale.total), 0)).where(
            Sale.tenant_id == t.id, Sale.created_at >= month_start
        )
    ) or 0
    return TenantAdminOut(
        id=t.id, name=t.name, owner_name=t.owner_name, owner_email=t.owner_email,
        owner_phone=t.owner_phone, business_type=t.business_type.value,
        region=t.region, district=t.district, plan=t.plan.value, status=t.status.value,
        tra_efd_serial=t.tra_efd_serial or "", created_at=t.created_at,
        branches_count=len(t.branches), products_count=int(pc), customers_count=int(cc),
        monthly_revenue=float(mr),
    )


@router.patch("/tenants/{tenant_id}", response_model=TenantAdminOut)
async def update_tenant_status(
    tenant_id: str,
    body: TenantStatusUpdate,
    user: Annotated[User, Depends(require_roles(UserRole.super_admin))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(
        select(Tenant).options(selectinload(Tenant.branches)).where(Tenant.id == tenant_id)
    )
    t = result.scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail="Tenant not found")
    if body.status:
        try:
            t.status = TenantStatus(body.status)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Invalid status: {body.status}") from e
    if body.plan:
        try:
            t.plan = SaaSPlanTier(body.plan)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Invalid plan: {body.plan}") from e
    await db.flush()
    return await get_tenant(tenant_id, user, db)


@router.post("/tenants/{tenant_id}/approve-kyc", response_model=TenantAdminOut)
async def approve_kyc(
    tenant_id: str,
    user: Annotated[User, Depends(require_roles(UserRole.super_admin))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await update_tenant_status(
        tenant_id, TenantStatusUpdate(status="active"), user, db
    )


@router.post("/tenants/{tenant_id}/suspend", response_model=TenantAdminOut)
async def suspend_tenant(
    tenant_id: str,
    user: Annotated[User, Depends(require_roles(UserRole.super_admin))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await update_tenant_status(
        tenant_id, TenantStatusUpdate(status="suspended"), user, db
    )


class ShowcaseItemOut(BaseModel):
    id: str
    title: str
    subtitle: str | None
    media_type: str
    media_url: str
    thumbnail_url: str | None
    link_url: str | None
    sort_order: int
    is_active: bool
    is_featured: bool
    created_by: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ShowcaseItemCreate(BaseModel):
    title: str
    subtitle: str | None = None
    media_type: str = "image"
    media_url: str
    thumbnail_url: str | None = None
    link_url: str | None = None
    sort_order: int = 0
    is_active: bool = True
    is_featured: bool = False


class ShowcaseItemUpdate(BaseModel):
    title: str | None = None
    subtitle: str | None = None
    media_type: str | None = None
    media_url: str | None = None
    thumbnail_url: str | None = None
    link_url: str | None = None
    sort_order: int | None = None
    is_active: bool | None = None
    is_featured: bool | None = None


@router.get("/showcase", response_model=list[ShowcaseItemOut])
async def admin_list_showcase(
    user: Annotated[User, Depends(require_roles(UserRole.super_admin))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(
        select(PlatformShowcaseItem).order_by(
            PlatformShowcaseItem.is_featured.desc(),
            PlatformShowcaseItem.sort_order.asc(),
        )
    )
    return list(result.scalars().all())


@router.post("/showcase", response_model=ShowcaseItemOut, status_code=status.HTTP_201_CREATED)
async def admin_create_showcase(
    body: ShowcaseItemCreate,
    user: Annotated[User, Depends(require_roles(UserRole.super_admin))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if body.is_featured:
        existing = await db.execute(
            select(PlatformShowcaseItem).where(PlatformShowcaseItem.is_featured.is_(True))
        )
        for row in existing.scalars().all():
            row.is_featured = False
    item = PlatformShowcaseItem(
        **body.model_dump(),
        created_by=user.name,
    )
    db.add(item)
    await db.flush()
    return item


@router.patch("/showcase/{item_id}", response_model=ShowcaseItemOut)
async def admin_update_showcase(
    item_id: str,
    body: ShowcaseItemUpdate,
    user: Annotated[User, Depends(require_roles(UserRole.super_admin))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(select(PlatformShowcaseItem).where(PlatformShowcaseItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Showcase item not found")
    updates = body.model_dump(exclude_unset=True)
    if updates.get("is_featured"):
        existing = await db.execute(
            select(PlatformShowcaseItem).where(
                PlatformShowcaseItem.is_featured.is_(True),
                PlatformShowcaseItem.id != item_id,
            )
        )
        for row in existing.scalars().all():
            row.is_featured = False
    for k, v in updates.items():
        setattr(item, k, v)
    await db.flush()
    return item


@router.delete("/showcase/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def admin_delete_showcase(
    item_id: str,
    user: Annotated[User, Depends(require_roles(UserRole.super_admin))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(select(PlatformShowcaseItem).where(PlatformShowcaseItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Showcase item not found")
    await db.delete(item)
