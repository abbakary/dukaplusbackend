import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, require_tenant, require_vendor_subscription
from app.database import get_db
from app.models import User
from app.models.accounting import HrPayrollContract, HrPayslip
from app.services.accounting_posting import post_payroll_journal

router = APIRouter(prefix="/tenant/payroll", tags=["payroll"], dependencies=[Depends(require_vendor_subscription)])


class ContractUpsert(BaseModel):
    staff_id: str
    staff_name: str = ""
    wage_monthly: float = 0
    structure_code: str = "standard"
    nssf_enabled: bool = True
    paye_enabled: bool = True
    active: bool = True


class PayslipRunRequest(BaseModel):
    period: str = Field(..., pattern=r"^\d{4}-\d{2}$")
    staff: list[dict] = Field(default_factory=list)


class PayrollAccountingPost(BaseModel):
    period: str = Field(..., pattern=r"^\d{4}-\d{2}$")
    branch_id: str | None = None
    gross: float = 0
    net: float = 0
    paye: float = 0
    nssf_total: float = 0
    nhif_total: float = 0
    heslb: float = 0
    sdl: float = 0
    wcf: float = 0
    employer_statutory: float = 0


@router.get("/contracts")
async def list_contracts(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    result = await db.execute(select(HrPayrollContract).where(HrPayrollContract.tenant_id == tenant_id))
    return [
        {
            "id": c.id,
            "staff_id": c.staff_id,
            "staff_name": c.staff_name,
            "wage_monthly": c.wage_monthly,
            "structure_code": c.structure_code,
            "nssf_enabled": c.nssf_enabled,
            "paye_enabled": c.paye_enabled,
            "active": c.active,
        }
        for c in result.scalars().all()
    ]


@router.put("/contracts")
async def upsert_contract(
    body: ContractUpsert,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    result = await db.execute(
        select(HrPayrollContract).where(
            HrPayrollContract.tenant_id == tenant_id,
            HrPayrollContract.staff_id == body.staff_id,
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        row = HrPayrollContract(tenant_id=tenant_id, staff_id=body.staff_id)
        db.add(row)
    row.staff_name = body.staff_name.strip() or row.staff_name
    row.wage_monthly = body.wage_monthly
    row.structure_code = body.structure_code
    row.nssf_enabled = body.nssf_enabled
    row.paye_enabled = body.paye_enabled
    row.active = body.active
    await db.flush()
    return {"id": row.id, "staff_id": row.staff_id}


@router.get("/payslips")
async def list_payslips(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    period: str | None = Query(None),
):
    tenant_id = require_tenant(user)
    q = select(HrPayslip).where(HrPayslip.tenant_id == tenant_id)
    if period:
        q = q.where(HrPayslip.period == period)
    result = await db.execute(q.order_by(HrPayslip.created_at.desc()).limit(200))
    return [
        {
            "id": p.id,
            "staff_id": p.staff_id,
            "staff_name": p.staff_name,
            "period": p.period,
            "gross_pay": p.gross_pay,
            "deductions": p.deductions,
            "net_pay": p.net_pay,
            "status": p.status,
            "payslip_number": p.payslip_number,
            "lines": json.loads(p.lines_json or "[]"),
        }
        for p in result.scalars().all()
    ]


@router.post("/payslips/run")
async def run_payslips(
    body: PayslipRunRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    if not body.staff:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No staff rows supplied.")

    created = []
    for row in body.staff:
        staff_id = str(row.get("staff_id") or row.get("staffId") or "")
        if not staff_id:
            continue
        gross = float(row.get("gross_pay") or row.get("baseSalary") or row.get("base_salary") or 0)
        deductions = float(row.get("deductions") or row.get("statutoryDeductions") or 0)
        net = float(row.get("net_pay") or row.get("netPayable") or max(gross - deductions, 0))
        lines = row.get("lines") or []
        slip = HrPayslip(
            tenant_id=tenant_id,
            staff_id=staff_id,
            staff_name=str(row.get("staff_name") or row.get("staffName") or ""),
            period=body.period,
            gross_pay=gross,
            deductions=deductions,
            net_pay=net,
            status=str(row.get("status") or "draft"),
            payslip_number=str(row.get("payslip_number") or row.get("payslipNumber") or f"PS-{body.period}-{staff_id[:8]}"),
            lines_json=json.dumps(lines),
        )
        db.add(slip)
        created.append(staff_id)
    await db.flush()
    return {"period": body.period, "created_count": len(created), "staff_ids": created}


@router.post("/post-to-accounting")
async def post_payroll_to_accounting(
    body: PayrollAccountingPost,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    if body.gross <= 0 or body.net < 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payroll totals.")
    try:
        entry = await post_payroll_journal(
            db,
            tenant_id=tenant_id,
            period=body.period,
            branch_id=body.branch_id,
            gross=body.gross,
            net=body.net,
            paye=body.paye,
            nssf_total=body.nssf_total,
            nhif_total=body.nhif_total,
            heslb=body.heslb,
            sdl=body.sdl,
            wcf=body.wcf,
            employer_statutory=body.employer_statutory,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await db.flush()
    return {"id": entry.id, "reference": entry.reference}
