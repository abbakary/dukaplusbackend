from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.branch_scope import get_staff_branch_id, is_tenant_wide_access
from app.core.deps import get_current_user, get_user_permissions, require_permission, require_tenant, require_vendor_subscription
from app.core.security import DEFAULT_PERMISSIONS, hash_password
from app.database import get_db
from app.models import (
    Branch,
    CalendarEvent,
    Expense,
    Product,
    PurchaseOrder,
    StaffMember,
    StaffRole,
    Supplier,
    StockMovement,
    User,
    UserRole,
)
from app.services.branch_service import BranchCreate, create_branch_with_admin, enrich_branch_row

router = APIRouter(tags=["extended"], dependencies=[Depends(require_vendor_subscription)])


# ── Schemas ───────────────────────────────────────────────────────────────────

class SupplierOut(BaseModel):
    id: str
    name: str
    contact_person: str
    phone: str
    email: str
    category: str
    payment_terms: str
    outstanding_payable: float
    lead_time_days: int
    rating: float
    model_config = {"from_attributes": True}


class SupplierCreate(BaseModel):
    name: str
    contact_person: str = ""
    phone: str = ""
    email: str = ""
    category: str = "General"
    payment_terms: str = "Net 30 Days"
    outstanding_payable: float = 0
    lead_time_days: int = 7
    rating: float = 5.0


class SupplierUpdate(BaseModel):
    name: str | None = None
    contact_person: str | None = None
    phone: str | None = None
    email: str | None = None
    category: str | None = None
    payment_terms: str | None = None
    outstanding_payable: float | None = None
    lead_time_days: int | None = None
    rating: float | None = None


class BranchOut(BaseModel):
    id: str
    name: str
    code: str
    branch_type: str
    status: str
    region: str
    district: str
    address: str
    phone: str
    tra_efd_serial: str = ""
    opening_hours: str = "08:00 - 20:00"
    manager_staff_id: str | None = None
    manager_name: str | None = None
    staff_count: int = 0
    created_at: str | None = None
    model_config = {"from_attributes": True}


class BranchCreateResponse(BranchOut):
    admin_email: str | None = None


class StaffOut(BaseModel):
    id: str
    name: str
    email: str
    phone: str
    role: str
    active: bool
    permissions: dict
    branch_id: str | None = None
    branch_name: str | None = None
    model_config = {"from_attributes": True}


class StaffCreate(BaseModel):
    name: str
    email: str
    phone: str = ""
    role: str = "Cashier"
    password: str
    branch_id: str | None = None


class StaffUpdate(BaseModel):
    name: str | None = None
    phone: str | None = None
    role: str | None = None
    active: bool | None = None
    permissions: dict | None = None


class StipendClaimRequest(BaseModel):
    food_amount: float = 5000
    transport_amount: float = 3000


class StipendClaimResponse(BaseModel):
    claimed: bool
    amount: float
    food_amount: float
    transport_amount: float
    staff_name: str
    expense_id: str
    already_claimed: bool = False


class ExpenseOut(BaseModel):
    id: str
    title: str
    category: str
    amount: float
    payment_method: str
    recipient: str
    status: str
    expense_date: datetime
    model_config = {"from_attributes": True}


class ExpenseCreate(BaseModel):
    title: str
    category: str
    amount: float
    payment_method: str = "cash_drawer"
    recipient: str = ""
    notes: str | None = None


class ExpenseUpdate(BaseModel):
    title: str | None = None
    category: str | None = None
    amount: float | None = None
    payment_method: str | None = None
    recipient: str | None = None
    status: str | None = None
    notes: str | None = None


class CalendarEventOut(BaseModel):
    id: str
    title: str
    category: str
    event_date: str
    event_time: str
    priority: str
    description: str
    assigned_to: str
    completed: bool
    metadata_json: dict
    model_config = {"from_attributes": True}


class CalendarEventCreate(BaseModel):
    title: str
    category: str = "general"
    event_date: str
    event_time: str = "09:00"
    priority: str = "medium"
    description: str = ""
    assigned_to: str = ""
    metadata_json: dict = {}


class CalendarEventUpdate(BaseModel):
    title: str | None = None
    category: str | None = None
    event_date: str | None = None
    event_time: str | None = None
    priority: str | None = None
    description: str | None = None
    assigned_to: str | None = None
    completed: bool | None = None
    metadata_json: dict | None = None


class POItemCreate(BaseModel):
    product_id: str | None = None
    product_name: str
    quantity: float
    unit_cost: float
    total: float | None = None
    batch_number: str | None = None
    expiry_date: str | None = None


class PurchaseOrderCreate(BaseModel):
    supplier_id: str
    items: list[POItemCreate]
    notes: str | None = None
    expected_date: str | None = None


class PurchaseOrderOut(BaseModel):
    id: str
    po_number: str
    supplier_id: str
    supplier_name: str
    status: str
    items: list
    subtotal: float
    total_amount: float
    paid_amount: float
    notes: str | None
    created_at: datetime
    model_config = {"from_attributes": True}


class POReceiveRequest(BaseModel):
    notes: str | None = None


async def _tenant_entity(db: AsyncSession, model, entity_id: str, tenant_id: str):
    result = await db.execute(select(model).where(model.id == entity_id, model.tenant_id == tenant_id))
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail=f"{model.__name__} not found")
    return row


# ── Suppliers ─────────────────────────────────────────────────────────────────

@router.get("/suppliers", response_model=list[SupplierOut])
async def list_suppliers(user: Annotated[User, Depends(get_current_user)], db: Annotated[AsyncSession, Depends(get_db)]):
    tid = require_tenant(user)
    if get_staff_branch_id(user):
        return []
    r = await db.execute(select(Supplier).where(Supplier.tenant_id == tid).order_by(Supplier.name))
    return r.scalars().all()


@router.post("/suppliers", response_model=SupplierOut, status_code=status.HTTP_201_CREATED)
async def create_supplier(
    body: SupplierCreate,
    user: Annotated[User, Depends(require_permission("canManageSuppliers"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tid = require_tenant(user)
    sup = Supplier(tenant_id=tid, **body.model_dump())
    db.add(sup)
    await db.flush()
    return sup


@router.patch("/suppliers/{supplier_id}", response_model=SupplierOut)
async def update_supplier(
    supplier_id: str,
    body: SupplierUpdate,
    user: Annotated[User, Depends(require_permission("canManageSuppliers"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tid = require_tenant(user)
    sup = await _tenant_entity(db, Supplier, supplier_id, tid)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(sup, k, v)
    await db.flush()
    return sup


@router.delete("/suppliers/{supplier_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_supplier(
    supplier_id: str,
    user: Annotated[User, Depends(require_permission("canManageSuppliers"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tid = require_tenant(user)
    sup = await _tenant_entity(db, Supplier, supplier_id, tid)
    await db.delete(sup)


# ── Branches ──────────────────────────────────────────────────────────────────

@router.get("/branches", response_model=list[BranchOut])
async def list_branches(user: Annotated[User, Depends(get_current_user)], db: Annotated[AsyncSession, Depends(get_db)]):
    tid = require_tenant(user)
    q = select(Branch).where(Branch.tenant_id == tid).order_by(Branch.name)
    if not is_tenant_wide_access(user) and user.staff and user.staff.branch_id:
        q = q.where(Branch.id == user.staff.branch_id)
    r = await db.execute(q)
    branches = r.scalars().all()
    out = []
    for b in branches:
        out.append(await enrich_branch_row(db, b))
    return out


@router.post("/branches", response_model=BranchCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_branch(
    body: BranchCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if not is_tenant_wide_access(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the owner can create branches")
    tenant = user.tenant
    if not tenant:
        raise HTTPException(status_code=400, detail="No tenant associated")
    branch, admin_staff = await create_branch_with_admin(db, tenant, body)
    payload = await enrich_branch_row(db, branch)
    payload["admin_email"] = admin_staff.email
    return payload


# ── Staff ─────────────────────────────────────────────────────────────────────

@router.get("/staff", response_model=list[StaffOut])
async def list_staff(user: Annotated[User, Depends(get_current_user)], db: Annotated[AsyncSession, Depends(get_db)]):
    tid = require_tenant(user)
    q = select(StaffMember).where(StaffMember.tenant_id == tid, StaffMember.active == True)  # noqa: E712
    if not is_tenant_wide_access(user) and user.staff and user.staff.branch_id:
        q = q.where(StaffMember.branch_id == user.staff.branch_id)
    r = await db.execute(q)
    rows = r.scalars().all()
    branch_names: dict[str, str] = {}
    branch_ids = {s.branch_id for s in rows if s.branch_id}
    if branch_ids:
        br = await db.execute(select(Branch).where(Branch.id.in_(branch_ids)))
        branch_names = {b.id: b.name for b in br.scalars().all()}
    return [
        StaffOut(
            id=s.id, name=s.name, email=s.email, phone=s.phone,
            role=s.role.value if hasattr(s.role, "value") else str(s.role),
            active=s.active, permissions=s.permissions or {},
            branch_id=s.branch_id,
            branch_name=branch_names.get(s.branch_id) if s.branch_id else None,
        )
        for s in rows
    ]


@router.post("/staff", response_model=StaffOut, status_code=status.HTTP_201_CREATED)
async def create_staff(
    body: StaffCreate,
    user: Annotated[User, Depends(require_permission("canViewProfitReports"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tid = require_tenant(user)
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")
    try:
        role = StaffRole(body.role)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid role: {body.role}") from e
    branch_id = body.branch_id
    staff_branch = get_staff_branch_id(user)
    if staff_branch:
        branch_id = staff_branch
    elif not branch_id:
        br = await db.execute(select(Branch).where(Branch.tenant_id == tid).limit(1))
        b = br.scalar_one_or_none()
        branch_id = b.id if b else None
    staff = StaffMember(
        tenant_id=tid, branch_id=branch_id, name=body.name, email=body.email,
        phone=body.phone, role=role,
        permissions=DEFAULT_PERMISSIONS.get(role.value, DEFAULT_PERMISSIONS["Cashier"]),
    )
    db.add(staff)
    await db.flush()
    db.add(User(
        email=body.email, hashed_password=hash_password(body.password),
        name=body.name, phone=body.phone, role=UserRole.vendor_staff,
        tenant_id=tid, staff_id=staff.id,
    ))
    await db.flush()
    return StaffOut(
        id=staff.id, name=staff.name, email=staff.email, phone=staff.phone,
        role=role.value, active=staff.active, permissions=staff.permissions,
    )


@router.patch("/staff/{staff_id}", response_model=StaffOut)
async def update_staff(
    staff_id: str,
    body: StaffUpdate,
    user: Annotated[User, Depends(require_permission("canViewProfitReports"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tid = require_tenant(user)
    staff = await _tenant_entity(db, StaffMember, staff_id, tid)
    data = body.model_dump(exclude_unset=True)
    if "role" in data:
        try:
            staff.role = StaffRole(data.pop("role"))
            if body.permissions is None:
                staff.permissions = DEFAULT_PERMISSIONS.get(staff.role.value, DEFAULT_PERMISSIONS["Cashier"])
        except ValueError as e:
            raise HTTPException(status_code=400, detail="Invalid role") from e
    for k, v in data.items():
        setattr(staff, k, v)
    await db.flush()
    return StaffOut(
        id=staff.id, name=staff.name, email=staff.email, phone=staff.phone,
        role=staff.role.value, active=staff.active, permissions=staff.permissions,
    )


@router.post("/staff/me/claim-stipend", response_model=StipendClaimResponse)
async def claim_my_daily_stipend(
    body: StipendClaimRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Staff self-service: cashiers and POS staff claim their own daily food & transport stipend."""
    perms = get_user_permissions(user)
    if not (perms.get("canPerformDailyClosing") or perms.get("canSellPOS")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing permission to claim daily stipend")
    if not user.staff_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No staff profile linked to this account")

    tid = require_tenant(user)
    result = await db.execute(
        select(StaffMember).where(StaffMember.id == user.staff_id, StaffMember.tenant_id == tid)
    )
    staff = result.scalar_one_or_none()
    if not staff:
        raise HTTPException(status_code=404, detail="Staff member not found")

    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    existing = await db.execute(
        select(Expense).where(
            Expense.tenant_id == tid,
            Expense.category == "daily_stipends_food_transport",
            Expense.recipient == staff.name,
            Expense.expense_date >= today_start,
        )
    )
    prior = existing.scalar_one_or_none()
    if prior:
        return StipendClaimResponse(
            claimed=True,
            amount=prior.amount,
            food_amount=body.food_amount,
            transport_amount=body.transport_amount,
            staff_name=staff.name,
            expense_id=prior.id,
            already_claimed=True,
        )

    total = max(0.0, body.food_amount) + max(0.0, body.transport_amount)
    if total <= 0:
        raise HTTPException(status_code=400, detail="Stipend amount must be greater than zero")

    exp = Expense(
        tenant_id=tid,
        title=f"Posho ya leo — {staff.name}",
        category="daily_stipends_food_transport",
        amount=total,
        payment_method="cash_drawer",
        recipient=staff.name,
        notes=f"Self-claim: Chakula {body.food_amount} + Nauli {body.transport_amount}",
        status="paid",
    )
    db.add(exp)
    await db.flush()

    return StipendClaimResponse(
        claimed=True,
        amount=total,
        food_amount=body.food_amount,
        transport_amount=body.transport_amount,
        staff_name=staff.name,
        expense_id=exp.id,
        already_claimed=False,
    )


# ── Expenses ──────────────────────────────────────────────────────────────────

@router.get("/expenses", response_model=list[ExpenseOut])
async def list_expenses(user: Annotated[User, Depends(get_current_user)], db: Annotated[AsyncSession, Depends(get_db)]):
    tid = require_tenant(user)
    if get_staff_branch_id(user):
        return []
    r = await db.execute(select(Expense).where(Expense.tenant_id == tid).order_by(Expense.expense_date.desc()))
    return r.scalars().all()


@router.post("/expenses", response_model=ExpenseOut, status_code=status.HTTP_201_CREATED)
async def create_expense(
    body: ExpenseCreate,
    user: Annotated[User, Depends(require_permission("canViewProfitReports"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tid = require_tenant(user)
    exp = Expense(tenant_id=tid, **body.model_dump())
    db.add(exp)
    await db.flush()
    return exp


@router.patch("/expenses/{expense_id}", response_model=ExpenseOut)
async def update_expense(
    expense_id: str,
    body: ExpenseUpdate,
    user: Annotated[User, Depends(require_permission("canViewProfitReports"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tid = require_tenant(user)
    exp = await _tenant_entity(db, Expense, expense_id, tid)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(exp, k, v)
    await db.flush()
    return exp


@router.delete("/expenses/{expense_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_expense(
    expense_id: str,
    user: Annotated[User, Depends(require_permission("canViewProfitReports"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tid = require_tenant(user)
    exp = await _tenant_entity(db, Expense, expense_id, tid)
    await db.delete(exp)


# ── Calendar ──────────────────────────────────────────────────────────────────

@router.get("/calendar/events", response_model=list[CalendarEventOut])
async def list_events(user: Annotated[User, Depends(get_current_user)], db: Annotated[AsyncSession, Depends(get_db)]):
    tid = require_tenant(user)
    r = await db.execute(select(CalendarEvent).where(CalendarEvent.tenant_id == tid).order_by(CalendarEvent.event_date))
    return r.scalars().all()


@router.post("/calendar/events", response_model=CalendarEventOut, status_code=status.HTTP_201_CREATED)
async def create_event(
    body: CalendarEventCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tid = require_tenant(user)
    ev = CalendarEvent(tenant_id=tid, **body.model_dump())
    db.add(ev)
    await db.flush()
    return ev


@router.patch("/calendar/events/{event_id}", response_model=CalendarEventOut)
async def update_event(
    event_id: str,
    body: CalendarEventUpdate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tid = require_tenant(user)
    ev = await _tenant_entity(db, CalendarEvent, event_id, tid)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(ev, k, v)
    await db.flush()
    return ev


@router.delete("/calendar/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_event(
    event_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tid = require_tenant(user)
    ev = await _tenant_entity(db, CalendarEvent, event_id, tid)
    await db.delete(ev)


# ── Purchase Orders ───────────────────────────────────────────────────────────

@router.get("/purchase-orders", response_model=list[PurchaseOrderOut])
async def list_purchase_orders(user: Annotated[User, Depends(get_current_user)], db: Annotated[AsyncSession, Depends(get_db)]):
    tid = require_tenant(user)
    r = await db.execute(select(PurchaseOrder).where(PurchaseOrder.tenant_id == tid).order_by(PurchaseOrder.created_at.desc()))
    return r.scalars().all()


@router.post("/purchase-orders", response_model=PurchaseOrderOut, status_code=status.HTTP_201_CREATED)
async def create_purchase_order(
    body: PurchaseOrderCreate,
    user: Annotated[User, Depends(require_permission("canManageSuppliers"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tid = require_tenant(user)
    sup = await _tenant_entity(db, Supplier, body.supplier_id, tid)
    items = []
    subtotal = 0.0
    for it in body.items:
        total = it.total if it.total is not None else it.quantity * it.unit_cost
        subtotal += total
        items.append({
            "product_id": it.product_id,
            "product_name": it.product_name,
            "quantity": it.quantity,
            "unit_cost": it.unit_cost,
            "total": total,
            "batch_number": it.batch_number,
            "expiry_date": it.expiry_date,
        })
    po_num = f"PO-{datetime.now(UTC).strftime('%Y%m%d')}-{len(items)}"
    po = PurchaseOrder(
        tenant_id=tid,
        po_number=po_num,
        supplier_id=sup.id,
        supplier_name=sup.name,
        status="sent",
        items=items,
        subtotal=subtotal,
        total_amount=subtotal,
        notes=body.notes,
    )
    db.add(po)
    await db.flush()
    return po


@router.post("/purchase-orders/{po_id}/receive", response_model=PurchaseOrderOut)
async def receive_purchase_order(
    po_id: str,
    body: POReceiveRequest,
    user: Annotated[User, Depends(require_permission("canModifyInventory"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tid = require_tenant(user)
    po = await _tenant_entity(db, PurchaseOrder, po_id, tid)
    if po.status == "received":
        raise HTTPException(status_code=400, detail="PO already received")

    for item in po.items:
        qty = float(item.get("quantity", 0))
        product_id = item.get("product_id")
        product = None
        if product_id:
            pr = await db.execute(select(Product).where(Product.id == product_id, Product.tenant_id == tid))
            product = pr.scalar_one_or_none()
        if not product:
            sku = f"PO-{po.po_number}-{item.get('product_name', '')[:8]}"
            product = Product(
                tenant_id=tid,
                name=item.get("product_name", "Imported Item"),
                category="Procurement",
                sku=sku,
                price=float(item.get("unit_cost", 0)) * 1.2,
                cost=float(item.get("unit_cost", 0)),
                stock=0,
                unit="pcs",
                batch_number=item.get("batch_number"),
                business_type=user.tenant.business_type.value if user.tenant else "retail",
            )
            db.add(product)
            await db.flush()

        prev = product.stock
        product.stock += qty
        if item.get("batch_number"):
            product.batch_number = item.get("batch_number")
        db.add(StockMovement(
            tenant_id=tid,
            product_id=product.id,
            product_name=product.name,
            sku=product.sku,
            movement_type="in_po",
            quantity=qty,
            previous_stock=prev,
            new_stock=product.stock,
            batch_number=item.get("batch_number"),
            reference_id=po.po_number,
            reference_type="PO",
            operator_name=user.name,
            notes=body.notes or f"Received PO {po.po_number}",
        ))

    po.status = "received"
    po.received_date = datetime.now(UTC)
    if body.notes:
        po.notes = f"{po.notes or ''} • {body.notes}".strip(" •")
    sup = await _tenant_entity(db, Supplier, po.supplier_id, tid)
    sup.outstanding_payable += po.total_amount - po.paid_amount
    await db.flush()
    return po
