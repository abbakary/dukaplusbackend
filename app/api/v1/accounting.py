import json
from datetime import date, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.branch_scope import resolve_branch_filter
from app.core.deps import get_current_user, require_tenant, require_vendor_subscription
from app.database import get_db
from app.models import Sale, User
from app.models.accounting import JournalEntry, JournalLine, LedgerAccount, SaleQuotation, VendorBill
from app.services.vendor_bill_service import post_vendor_bill, register_bill_payment
from app.services.accounting_defaults import ensure_default_chart
from app.services.accounting_posting import COMPLETED_SALE_STATUSES, post_sale_journal
from app.services.accounting_reports import build_report_bundle

router = APIRouter(prefix="/tenant/accounting", tags=["accounting"], dependencies=[Depends(require_vendor_subscription)])


class JournalLineIn(BaseModel):
    account_code: str
    label: str = ""
    debit: float = 0
    credit: float = 0


class JournalEntryIn(BaseModel):
    entry_date: date
    reference: str = ""
    memo: str = ""
    branch_id: str | None = None
    lines: list[JournalLineIn] = Field(min_length=2)


@router.get("/accounts")
async def list_accounts(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    rows = await ensure_default_chart(db, tenant_id)
    return [
        {
            "id": a.id,
            "code": a.code,
            "name": a.name,
            "account_type": a.account_type,
            "is_active": a.is_active,
        }
        for a in sorted(rows, key=lambda x: x.code)
    ]


@router.get("/entries")
async def list_entries(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(40, ge=1, le=200),
):
    tenant_id = require_tenant(user)
    result = await db.execute(
        select(JournalEntry)
        .where(JournalEntry.tenant_id == tenant_id)
        .order_by(JournalEntry.posted_at.desc())
        .limit(limit)
    )
    # JournalEntry.lines relationship not defined — load lines manually
    entries = result.scalars().all()
    out = []
    for entry in entries:
        lines_result = await db.execute(select(JournalLine).where(JournalLine.entry_id == entry.id))
        lines = lines_result.scalars().all()
        acct_ids = {ln.account_id for ln in lines}
        acct_map: dict[str, LedgerAccount] = {}
        if acct_ids:
            acct_result = await db.execute(select(LedgerAccount).where(LedgerAccount.id.in_(acct_ids)))
            for acct in acct_result.scalars().all():
                acct_map[acct.id] = acct
        out.append(
            {
                "id": entry.id,
                "entry_date": entry.entry_date.isoformat(),
                "reference": entry.reference,
                "memo": entry.memo,
                "source": entry.source,
                "branch_id": entry.branch_id,
                "lines": [
                    {
                        "account_code": acct_map.get(ln.account_id).code if ln.account_id in acct_map else "",
                        "account_name": acct_map.get(ln.account_id).name if ln.account_id in acct_map else "",
                        "label": ln.label,
                        "debit": ln.debit,
                        "credit": ln.credit,
                    }
                    for ln in lines
                ],
            }
        )
    return out


@router.post("/entries")
async def create_entry(
    body: JournalEntryIn,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    accounts = await ensure_default_chart(db, tenant_id)
    by_code = {a.code: a for a in accounts}
    total_debit = sum(l.debit for l in body.lines)
    total_credit = sum(l.credit for l in body.lines)
    if round(total_debit, 2) != round(total_credit, 2) or total_debit <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Journal entry must balance debits and credits.")

    entry = JournalEntry(
        tenant_id=tenant_id,
        branch_id=body.branch_id,
        entry_date=body.entry_date,
        reference=body.reference.strip(),
        memo=body.memo.strip(),
        source="manual",
    )
    db.add(entry)
    await db.flush()

    for line in body.lines:
        acct = by_code.get(line.account_code)
        if not acct:
            raise HTTPException(status_code=400, detail=f"Unknown account code {line.account_code}")
        db.add(
            JournalLine(
                entry_id=entry.id,
                account_id=acct.id,
                label=line.label,
                debit=line.debit,
                credit=line.credit,
            )
        )
    await db.flush()
    return {"id": entry.id, "reference": entry.reference}


@router.get("/reports/trial-balance")
async def trial_balance(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    accounts = await ensure_default_chart(db, tenant_id)
    acct_by_id = {a.id: a for a in accounts}

    lines_result = await db.execute(
        select(JournalLine, JournalEntry)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(JournalEntry.tenant_id == tenant_id)
    )
    totals: dict[str, dict[str, float]] = {}
    for line, _entry in lines_result.all():
        acct = acct_by_id.get(line.account_id)
        if not acct:
            continue
        bucket = totals.setdefault(acct.code, {"debit": 0.0, "credit": 0.0, "name": acct.name, "type": acct.account_type})
        bucket["debit"] += line.debit
        bucket["credit"] += line.credit

    rows = []
    for code in sorted(totals.keys()):
        t = totals[code]
        balance = round(t["debit"] - t["credit"], 2)
        rows.append(
            {
                "code": code,
                "name": t["name"],
                "account_type": t["type"],
                "debit": round(t["debit"], 2),
                "credit": round(t["credit"], 2),
                "balance": balance,
            }
        )
    return {"as_of": date.today().isoformat(), "rows": rows}


@router.get("/reports/bundle")
async def accounting_reports_bundle(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    books_mode: str = Query("standard", pattern="^(standard|tra)$"),
    branch_id: str | None = Query(None),
):
    tenant_id = require_tenant(user)
    effective_branch = resolve_branch_filter(user, branch_id)
    return await build_report_bundle(
        db,
        tenant_id=tenant_id,
        branch_id=effective_branch,
        books_mode=books_mode,
    )


@router.post("/sync-from-operations")
async def sync_accounting_from_operations(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    branch_id: str | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
):
    """Backfill journal entries from completed POS sales not yet posted."""
    tenant_id = require_tenant(user)
    effective_branch = resolve_branch_filter(user, branch_id)
    q = select(Sale).where(Sale.tenant_id == tenant_id, Sale.status.in_(tuple(COMPLETED_SALE_STATUSES)))
    if effective_branch:
        q = q.where(Sale.branch_id == effective_branch)
    q = q.order_by(Sale.created_at.desc()).limit(limit)
    sales = (await db.execute(q)).scalars().all()
    posted = 0
    for sale in sales:
        before = await db.execute(
            select(JournalEntry.id).where(
                JournalEntry.tenant_id == tenant_id,
                JournalEntry.source == "pos_sale",
                JournalEntry.source_id == sale.id,
            )
        )
        if before.scalar_one_or_none():
            continue
        await post_sale_journal(
            db,
            tenant_id=tenant_id,
            sale=sale,
            include_vat=float(sale.vat_amount or 0) > 0,
        )
        posted += 1
    await db.flush()
    return {"posted": posted, "scanned": len(sales)}


class VendorBillLineIn(BaseModel):
    label: str = ""
    account_code: str = "5100"
    quantity: float = 1
    price_unit: float = 0
    tax_rate: float = 0
    amount: float | None = None


class VendorBillIn(BaseModel):
    vendor_name: str
    vendor_id: str | None = None
    vendor_bill_ref: str = ""
    bill_date: date | None = None
    due_date: date | None = None
    lines: list[VendorBillLineIn] = Field(default_factory=list)
    notes: str = ""


class VendorPaymentIn(BaseModel):
    amount: float
    journal_code: str = "1000"
    reference: str = ""


class QuotationLineIn(BaseModel):
    product_name: str
    quantity: float = 1
    price_unit: float = 0
    tax_rate: float = 18


class QuotationIn(BaseModel):
    customer_name: str
    customer_id: str | None = None
    validity_days: int = 14
    lines: list[QuotationLineIn] = Field(default_factory=list)
    terms: str = ""


def _bill_payload(b: VendorBill) -> dict[str, Any]:
    return {
        "id": b.id,
        "name": b.name,
        "state": b.state,
        "payment_state": b.payment_state,
        "vendor_name": b.vendor_name,
        "vendor_id": b.vendor_id,
        "vendor_bill_ref": b.vendor_bill_ref,
        "bill_date": b.bill_date.isoformat(),
        "due_date": b.due_date.isoformat() if b.due_date else None,
        "amount_untaxed": b.amount_untaxed,
        "amount_tax": b.amount_tax,
        "amount_total": b.amount_total,
        "amount_residual": b.amount_residual,
        "lines": json.loads(b.lines_json or "[]"),
        "notes": b.notes,
        "journal_entry_id": b.journal_entry_id,
    }


def _compute_bill_amounts(lines: list[VendorBillLineIn]) -> tuple[float, float, float, list[dict]]:
    out_lines: list[dict] = []
    untaxed = 0.0
    tax = 0.0
    for ln in lines:
        amt = ln.amount if ln.amount is not None else ln.quantity * ln.price_unit
        line_tax = amt * (ln.tax_rate / 100) if ln.tax_rate else 0
        untaxed += amt
        tax += line_tax
        out_lines.append(
            {
                "label": ln.label,
                "account_code": ln.account_code,
                "quantity": ln.quantity,
                "price_unit": ln.price_unit,
                "tax_rate": ln.tax_rate,
                "amount": round(amt, 2),
            }
        )
    total = untaxed + tax
    return round(untaxed, 2), round(tax, 2), round(total, 2), out_lines


@router.get("/bills")
async def list_vendor_bills(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    state: str | None = Query(None),
):
    tenant_id = require_tenant(user)
    q = select(VendorBill).where(VendorBill.tenant_id == tenant_id).order_by(VendorBill.created_at.desc())
    if state:
        q = q.where(VendorBill.state == state)
    rows = (await db.execute(q.limit(100))).scalars().all()
    return [_bill_payload(b) for b in rows]


@router.post("/bills")
async def create_vendor_bill(
    body: VendorBillIn,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    bill_date = body.bill_date or date.today()
    untaxed, tax, total, out_lines = _compute_bill_amounts(body.lines)
    bill = VendorBill(
        tenant_id=tenant_id,
        name="Draft",
        state="draft",
        vendor_name=body.vendor_name.strip(),
        vendor_id=body.vendor_id,
        vendor_bill_ref=body.vendor_bill_ref.strip(),
        bill_date=bill_date,
        due_date=body.due_date or bill_date + timedelta(days=30),
        amount_untaxed=untaxed,
        amount_tax=tax,
        amount_total=total,
        amount_residual=total,
        lines_json=json.dumps(out_lines),
        notes=body.notes,
    )
    db.add(bill)
    await db.flush()
    return _bill_payload(bill)


@router.get("/bills/{bill_id}")
async def get_vendor_bill(
    bill_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    bill = await db.get(VendorBill, bill_id)
    if not bill or bill.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Bill not found")
    return _bill_payload(bill)


@router.post("/bills/{bill_id}/post")
async def confirm_vendor_bill(
    bill_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    bill = await db.get(VendorBill, bill_id)
    if not bill or bill.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Bill not found")
    if bill.state == "posted":
        return _bill_payload(bill)
    if not bill.lines_json or bill.lines_json == "[]":
        raise HTTPException(status_code=400, detail="Add at least one invoice line")
    await post_vendor_bill(db, bill)
    return _bill_payload(bill)


@router.post("/bills/{bill_id}/pay")
async def pay_vendor_bill(
    bill_id: str,
    body: VendorPaymentIn,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    bill = await db.get(VendorBill, bill_id)
    if not bill or bill.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Bill not found")
    await register_bill_payment(db, bill, body.amount, journal_code=body.journal_code, reference=body.reference)
    return _bill_payload(bill)


@router.get("/quotations")
async def list_quotations(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    rows = (
        await db.execute(
            select(SaleQuotation).where(SaleQuotation.tenant_id == tenant_id).order_by(SaleQuotation.created_at.desc())
        )
    ).scalars().all()
    return [
        {
            "id": q.id,
            "name": q.name,
            "state": q.state,
            "customer_name": q.customer_name,
            "validity_date": q.validity_date.isoformat() if q.validity_date else None,
            "amount_total": q.amount_total,
            "lines": json.loads(q.lines_json or "[]"),
        }
        for q in rows
    ]


@router.post("/quotations")
async def create_quotation(
    body: QuotationIn,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    lines_out: list[dict] = []
    untaxed = 0.0
    tax = 0.0
    for ln in body.lines:
        sub = ln.quantity * ln.price_unit
        t = sub * (ln.tax_rate / 100)
        untaxed += sub
        tax += t
        lines_out.append(
            {
                "product_name": ln.product_name,
                "quantity": ln.quantity,
                "price_unit": ln.price_unit,
                "tax_rate": ln.tax_rate,
                "amount": round(sub, 2),
            }
        )
    count = (
        await db.scalar(select(func.count()).select_from(SaleQuotation).where(SaleQuotation.tenant_id == tenant_id))
        or 0
    )
    q = SaleQuotation(
        tenant_id=tenant_id,
        name=f"QT/{date.today().year}/{int(count) + 1:04d}",
        state="draft",
        customer_name=body.customer_name.strip(),
        customer_id=body.customer_id,
        validity_date=date.today() + timedelta(days=body.validity_days),
        amount_untaxed=round(untaxed, 2),
        amount_tax=round(tax, 2),
        amount_total=round(untaxed + tax, 2),
        lines_json=json.dumps(lines_out),
        terms=body.terms,
    )
    db.add(q)
    await db.flush()
    return {
        "id": q.id,
        "name": q.name,
        "state": q.state,
        "customer_name": q.customer_name,
        "amount_total": q.amount_total,
        "lines": lines_out,
    }
