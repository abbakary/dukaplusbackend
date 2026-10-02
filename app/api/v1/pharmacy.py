from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.branch_scope import resolve_branch_filter
from app.core.deps import get_current_user, require_tenant, require_vendor_subscription
from app.database import get_db
from app.models import Prescription, PrescriptionStatus, Product, User
from app.services.inventory_alerts import build_inventory_alerts

router = APIRouter(prefix="/pharmacy", tags=["pharmacy"], dependencies=[Depends(require_vendor_subscription)])


class PrescriptionCreate(BaseModel):
    patient_name: str = Field(min_length=2, max_length=255)
    doctor_name: str = Field(min_length=2, max_length=255)
    doctor_license: str = ""
    prescription_number: str = ""
    product_id: str | None = None
    product_name: str = ""
    customer_phone: str = ""
    quantity_requested: float = Field(default=1, gt=0)
    notes: str | None = None


class PrescriptionUpdate(BaseModel):
    status: PrescriptionStatus | None = None
    notes: str | None = None
    sale_id: str | None = None


class PrescriptionResponse(BaseModel):
    id: str
    tenant_id: str
    branch_id: str | None
    product_id: str | None
    product_name: str
    patient_name: str
    customer_phone: str
    doctor_name: str
    doctor_license: str
    prescription_number: str
    quantity_requested: float
    status: str
    notes: str | None
    verified_by_name: str | None
    verified_by_user_id: str | None
    sale_id: str | None
    created_at: str
    updated_at: str


def _rx_response(r: Prescription) -> PrescriptionResponse:
    return PrescriptionResponse(
        id=r.id,
        tenant_id=r.tenant_id,
        branch_id=r.branch_id,
        product_id=r.product_id,
        product_name=r.product_name,
        patient_name=r.patient_name,
        customer_phone=r.customer_phone,
        doctor_name=r.doctor_name,
        doctor_license=r.doctor_license,
        prescription_number=r.prescription_number,
        quantity_requested=r.quantity_requested,
        status=r.status.value,
        notes=r.notes,
        verified_by_name=r.verified_by_name,
        verified_by_user_id=r.verified_by_user_id,
        sale_id=r.sale_id,
        created_at=r.created_at.isoformat() if r.created_at else "",
        updated_at=r.updated_at.isoformat() if r.updated_at else "",
    )


@router.get("/inventory-alerts")
async def inventory_alerts(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    branch_id: str | None = Query(None),
) -> dict[str, Any]:
    require_tenant(user)
    effective = resolve_branch_filter(user, branch_id)
    return await build_inventory_alerts(db, user, branch_id=effective)


@router.get("/prescriptions", response_model=list[PrescriptionResponse])
async def list_prescriptions(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    status_filter: PrescriptionStatus | None = Query(None, alias="status"),
    limit: int = Query(100, le=200),
):
    tenant_id = require_tenant(user)
    q = select(Prescription).where(Prescription.tenant_id == tenant_id).order_by(Prescription.created_at.desc())
    if status_filter:
        q = q.where(Prescription.status == status_filter)
    q = q.limit(limit)
    rows = (await db.execute(q)).scalars().all()
    return [_rx_response(r) for r in rows]


@router.post("/prescriptions", response_model=PrescriptionResponse, status_code=status.HTTP_201_CREATED)
async def create_prescription(
    body: PrescriptionCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    branch_id = user.staff.branch_id if getattr(user, "staff", None) else None
    product_name = body.product_name.strip()
    product_id = body.product_id
    if product_id:
        prod = await db.get(Product, product_id)
        if not prod or prod.tenant_id != tenant_id:
            raise HTTPException(status_code=404, detail="Product not found")
        product_name = prod.name
        if not prod.requires_prescription:
            raise HTTPException(status_code=400, detail="This product is over-the-counter — no Rx required")

    rx = Prescription(
        tenant_id=tenant_id,
        branch_id=branch_id,
        product_id=product_id,
        product_name=product_name,
        patient_name=body.patient_name.strip(),
        customer_phone=body.customer_phone.strip(),
        doctor_name=body.doctor_name.strip(),
        doctor_license=body.doctor_license.strip(),
        prescription_number=body.prescription_number.strip(),
        quantity_requested=body.quantity_requested,
        notes=body.notes,
        status=PrescriptionStatus.pending,
    )
    db.add(rx)
    await db.flush()
    return _rx_response(rx)


@router.patch("/prescriptions/{prescription_id}", response_model=PrescriptionResponse)
async def update_prescription(
    prescription_id: str,
    body: PrescriptionUpdate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    rx = await db.get(Prescription, prescription_id)
    if not rx or rx.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Prescription not found")

    if body.status is not None:
        rx.status = body.status
        if body.status in (PrescriptionStatus.approved, PrescriptionStatus.rejected, PrescriptionStatus.dispensed):
            rx.verified_by_user_id = user.id
            rx.verified_by_name = user.name
    if body.notes is not None:
        rx.notes = body.notes
    if body.sale_id is not None:
        rx.sale_id = body.sale_id
        if rx.status == PrescriptionStatus.approved:
            rx.status = PrescriptionStatus.dispensed
    rx.updated_at = datetime.now(UTC)
    await db.flush()
    return _rx_response(rx)


@router.get("/prescriptions/summary")
async def prescriptions_summary(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    pending = await db.scalar(
        select(func.count(Prescription.id)).where(
            Prescription.tenant_id == tenant_id,
            Prescription.status == PrescriptionStatus.pending,
        )
    )
    approved = await db.scalar(
        select(func.count(Prescription.id)).where(
            Prescription.tenant_id == tenant_id,
            Prescription.status == PrescriptionStatus.approved,
        )
    )
    return {"pending": int(pending or 0), "approved": int(approved or 0)}
