import json
from datetime import date, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.branch_scope import assert_branch_record_access, branch_id_filter, resolve_branch_filter
from app.core.branch_scope import resolve_sale_branch_id
from app.services.branch_service import get_tenant_default_branch_id
from app.core.deps import get_current_user, require_tenant, require_vendor_subscription
from app.database import get_db
from app.models import Branch, PurchaseOrder, Sale, Tenant, User
from app.services.accounting_document_pdf import render_quotation_html, render_vendor_bill_html
from app.services.bank_reconciliation_service import (
    add_statement_line,
    create_statement,
    list_statements,
    reconcile_line,
    statement_lines,
    suggest_move_lines,
)
from app.services.customer_invoice_service import (
    get_customer_invoice_detail,
    list_customer_invoices,
    register_customer_payment_move,
)
from app.services.fiscal_position_service import ensure_default_fiscal_positions
from app.services.purchase_matching_service import link_bill_to_po, list_matches_for_bill, list_po_candidates
from app.services.report_xlsx_engine import report_payload_to_xlsx_bytes
from app.services.vendor_catalog_service import build_vendor_catalog
from app.models.accounting import (
    AccBillPurchaseMatch,
    AccMove,
    AccMoveLine,
    AccPayment,
    JournalEntry,
    JournalLine,
    LedgerAccount,
    SaleQuotation,
    VendorBill,
)
from app.services.db_schema_patches import ensure_accounting_schema
from app.services.acc_posting_service import AccPostingError
from app.services.vendor_bill_service import post_vendor_bill, register_bill_payment
from app.services.accounting_defaults import ensure_default_chart
from app.services.accounting_posting import COMPLETED_SALE_STATUSES, post_sale_journal
from app.services.accounting_reports import build_report_bundle
from app.services.odoo_style_reports import REPORT_CATALOG, run_odoo_report
from app.services.report_pdf_html import payload_to_body_html, render_report_html


async def _ensure_accounting_schema_dep(db: Annotated[AsyncSession, Depends(get_db)]) -> None:
    await ensure_accounting_schema(db)


router = APIRouter(
    prefix="/tenant/accounting",
    tags=["accounting"],
    dependencies=[Depends(require_vendor_subscription), Depends(_ensure_accounting_schema_dep)],
)


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


@router.get("/reports/catalog")
async def accounting_reports_catalog(
    user: Annotated[User, Depends(get_current_user)],
):
    require_tenant(user)
    return {"reports": REPORT_CATALOG}


@router.get("/reports/run/{report_key}")
async def accounting_run_report(
    report_key: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    books_mode: str = Query("standard", pattern="^(standard|tra)$"),
    branch_id: str | None = Query(None),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    target_move: str = Query("posted", pattern="^(posted|all)$"),
):
    tenant_id = require_tenant(user)
    effective_branch = resolve_branch_filter(user, branch_id)
    return await run_odoo_report(
        db,
        report_key=report_key,
        tenant_id=tenant_id,
        branch_id=effective_branch,
        books_mode=books_mode,
        date_from=date_from,
        date_to=date_to,
        target_move=target_move,
    )


@router.get("/reports/run/{report_key}/pdf", response_class=HTMLResponse)
async def accounting_run_report_pdf(
    report_key: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    books_mode: str = Query("standard", pattern="^(standard|tra)$"),
    branch_id: str | None = Query(None),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    target_move: str = Query("posted", pattern="^(posted|all)$"),
):
    tenant_id = require_tenant(user)
    effective_branch = resolve_branch_filter(user, branch_id)
    payload = await run_odoo_report(
        db,
        report_key=report_key,
        tenant_id=tenant_id,
        branch_id=effective_branch,
        books_mode=books_mode,
        date_from=date_from,
        date_to=date_to,
        target_move=target_move,
    )
    tenant = await db.get(Tenant, tenant_id)
    business_name = tenant.name if tenant else "Business"
    catalog = next((r for r in REPORT_CATALOG if r["key"] == report_key), None)
    title = (catalog or {}).get("name_en") or report_key.replace("_", " ").title()
    body = payload_to_body_html(payload)
    filters_html = (
        f'<div class="meta" style="margin-bottom:12px"><em>Filters (Zalongwa-style): '
        f'posted moves · books {books_mode} · branch {effective_branch or "all"}</em></div>'
    )
    html = render_report_html(
        business_name=business_name,
        report_title=title,
        meta={
            "date_from": payload.get("date_from"),
            "date_to": payload.get("date_to"),
            "target_move": payload.get("target_move"),
            "filters_html": filters_html,
        },
        body_html=body,
    )
    return HTMLResponse(content=html)


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
    product_id: str | None = None
    product_name: str = ""
    sku: str = ""
    unit: str = "pcs"
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
    branch_id: str | None = None


class VendorPaymentIn(BaseModel):
    amount: float
    journal_code: str = "1000"
    reference: str = ""


class QuotationLineIn(BaseModel):
    product_name: str = ""
    label: str = ""
    product_id: str | None = None
    sku: str = ""
    unit: str = "pcs"
    quantity: float = 1
    price_unit: float = 0
    tax_rate: float = 18


class QuotationIn(BaseModel):
    customer_name: str
    customer_id: str | None = None
    validity_days: int = 14
    validity_date: date | None = None
    quotation_date: date | None = None
    payment_terms: str = "immediate"
    lines: list[QuotationLineIn] = Field(default_factory=list)
    terms: str = ""
    branch_id: str | None = None


async def _apply_doc_branch_filter(q, column, user: User, db: AsyncSession, tenant_id: str, branch_id: str | None):
    effective = resolve_branch_filter(user, branch_id)
    if not effective:
        return q
    hq = await get_tenant_default_branch_id(db, tenant_id)
    clause = branch_id_filter(column, effective, hq)
    if clause is not None:
        return q.where(clause)
    return q


def _record_matches_branch_filter(record_branch_id: str | None, effective: str | None, hq_branch_id: str | None) -> bool:
    if not effective:
        return True
    if hq_branch_id and effective == hq_branch_id:
        return record_branch_id in (None, effective)
    return record_branch_id == effective


async def _load_vendor_bill(
    db: AsyncSession,
    user: User,
    tenant_id: str,
    bill_id: str,
    *,
    branch_id: str | None = None,
) -> VendorBill:
    bill = await db.get(VendorBill, bill_id)
    if not bill or bill.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Bill not found")
    assert_branch_record_access(user, bill.branch_id, label="bill")
    effective = resolve_branch_filter(user, branch_id)
    if effective:
        hq = await get_tenant_default_branch_id(db, tenant_id)
        if not _record_matches_branch_filter(bill.branch_id, effective, hq):
            raise HTTPException(status_code=404, detail="Bill not found")
    return bill


async def _load_quotation(
    db: AsyncSession,
    user: User,
    tenant_id: str,
    quotation_id: str,
    *,
    branch_id: str | None = None,
) -> SaleQuotation:
    q = await db.get(SaleQuotation, quotation_id)
    if not q or q.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Quotation not found")
    assert_branch_record_access(user, q.branch_id, label="quotation")
    effective = resolve_branch_filter(user, branch_id)
    if effective:
        hq = await get_tenant_default_branch_id(db, tenant_id)
        if not _record_matches_branch_filter(q.branch_id, effective, hq):
            raise HTTPException(status_code=404, detail="Quotation not found")
    return q


async def _tenant_company(db: AsyncSession, tenant_id: str) -> dict[str, Any]:
    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        return {"name": "Duka+"}
    branch = await db.scalar(select(Branch).where(Branch.tenant_id == tenant_id).limit(1))
    addr = branch.address if branch else ""
    return {
        "name": tenant.name,
        "tin": tenant.tin_number or "",
        "vrn": "",
        "address": addr or f"{tenant.region}, Tanzania",
        "phone": tenant.owner_phone or "",
    }


async def _bill_payload(db: AsyncSession, b: VendorBill) -> dict[str, Any]:
    payments_count = int(
        await db.scalar(
            select(func.count(AccPayment.id)).where(
                AccPayment.tenant_id == b.tenant_id,
                AccPayment.bill_id == b.id,
            )
        )
        or 0
    )
    purchase_matching_count = int(
        await db.scalar(
            select(func.count(AccBillPurchaseMatch.id)).where(
                AccBillPurchaseMatch.tenant_id == b.tenant_id,
                AccBillPurchaseMatch.bill_id == b.id,
            )
        )
        or 0
    )
    po_candidates_count = 0
    if b.vendor_id:
        po_candidates_count = int(
            await db.scalar(
                select(func.count(PurchaseOrder.id)).where(
                    PurchaseOrder.tenant_id == b.tenant_id,
                    PurchaseOrder.supplier_id == b.vendor_id,
                    PurchaseOrder.status.in_(("received", "partial", "confirmed")),
                )
            )
            or 0
        )

    journal_items: list[dict[str, Any]] = []
    move_id = b.acc_move_id
    if not move_id:
        move_id = await db.scalar(
            select(AccMove.id).where(AccMove.source_type == "vendor_bill", AccMove.source_id == b.id)
        )
    if move_id:
        accounts = {
            a.id: a
            for a in (await db.execute(select(LedgerAccount).where(LedgerAccount.tenant_id == b.tenant_id))).scalars()
        }
        move = await db.get(AccMove, move_id)
        ml_rows = (
            await db.execute(select(AccMoveLine).where(AccMoveLine.move_id == move_id).order_by(AccMoveLine.id))
        ).scalars().all()
        for ln in ml_rows:
            acct = accounts.get(ln.account_id)
            journal_items.append(
                {
                    "account_code": acct.code if acct else "",
                    "account_name": acct.name if acct else "",
                    "label": ln.name,
                    "debit": ln.debit,
                    "credit": ln.credit,
                    "partner_name": ln.partner_name,
                }
            )
        journal_preview = {
            "move_name": move.name if move else "",
            "move_id": move_id,
            "lines": journal_items,
        }
    else:
        journal_preview = None

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
        "acc_move_id": b.acc_move_id,
        "journal_items": journal_items,
        "journal_preview": journal_preview,
        "payments_count": payments_count,
        "purchase_matching_count": purchase_matching_count,
        "purchase_order_id": b.purchase_order_id,
        "branch_id": b.branch_id,
        "po_candidates_count": po_candidates_count,
        "smart_buttons": {
            "payments": payments_count,
            "purchase_matching": purchase_matching_count,
        },
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
        pname = (ln.label or ln.product_name or "").strip()
        out_lines.append(
            {
                "label": pname or "Line",
                "product_id": ln.product_id,
                "product_name": ln.product_name or pname,
                "sku": ln.sku,
                "unit": ln.unit or "pcs",
                "account_code": ln.account_code,
                "quantity": ln.quantity,
                "price_unit": ln.price_unit,
                "tax_rate": ln.tax_rate,
                "amount": round(amt, 2),
            }
        )
    total = untaxed + tax
    return round(untaxed, 2), round(tax, 2), round(total, 2), out_lines


@router.get("/chart-accounts")
async def list_chart_accounts(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    account_type: str | None = Query(None),
):
    tenant_id = require_tenant(user)
    await ensure_default_chart(db, tenant_id)
    q = select(LedgerAccount).where(LedgerAccount.tenant_id == tenant_id, LedgerAccount.is_active.is_(True))
    if account_type:
        q = q.where(LedgerAccount.account_type == account_type)
    rows = (await db.execute(q.order_by(LedgerAccount.code))).scalars().all()
    return [{"code": a.code, "name": a.name, "account_type": a.account_type} for a in rows]


@router.get("/vendor-catalog")
async def vendor_product_catalog(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    vendor_id: str | None = Query(None),
):
    tenant_id = require_tenant(user)
    items, vendor_history_count = await build_vendor_catalog(db, tenant_id=tenant_id, vendor_id=vendor_id)
    return {"items": items, "vendor_history_count": vendor_history_count}


@router.get("/bills")
async def list_vendor_bills(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    state: str | None = Query(None),
    branch_id: str | None = Query(None),
):
    tenant_id = require_tenant(user)
    q = select(VendorBill).where(VendorBill.tenant_id == tenant_id).order_by(VendorBill.created_at.desc())
    q = await _apply_doc_branch_filter(q, VendorBill.branch_id, user, db, tenant_id, branch_id)
    if state:
        q = q.where(VendorBill.state == state)
    rows = (await db.execute(q.limit(100))).scalars().all()
    out: list[dict[str, Any]] = []
    for b in rows:
        out.append(await _bill_payload(db, b))
    return out


@router.post("/bills")
async def create_vendor_bill(
    body: VendorBillIn,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    bill_date = body.bill_date or date.today()
    untaxed, tax, total, out_lines = _compute_bill_amounts(body.lines)
    record_branch = await resolve_sale_branch_id(db, user, tenant_id, body.branch_id)
    bill = VendorBill(
        tenant_id=tenant_id,
        branch_id=record_branch,
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
    return await _bill_payload(db, bill)


@router.get("/bills/{bill_id}")
async def get_vendor_bill(
    bill_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    branch_id: str | None = Query(None),
):
    tenant_id = require_tenant(user)
    bill = await _load_vendor_bill(db, user, tenant_id, bill_id, branch_id=branch_id)
    return await _bill_payload(db, bill)


@router.patch("/bills/{bill_id}")
async def update_vendor_bill(
    bill_id: str,
    body: VendorBillIn,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    bill = await _load_vendor_bill(db, user, tenant_id, bill_id, branch_id=body.branch_id)
    if bill.state != "draft":
        raise HTTPException(status_code=400, detail="Only draft bills can be edited")
    bill_date = body.bill_date or bill.bill_date
    untaxed, tax, total, out_lines = _compute_bill_amounts(body.lines)
    bill.vendor_name = body.vendor_name.strip()
    bill.vendor_id = body.vendor_id
    bill.vendor_bill_ref = body.vendor_bill_ref.strip()
    bill.bill_date = bill_date
    bill.due_date = body.due_date or bill.due_date or bill_date + timedelta(days=30)
    bill.amount_untaxed = untaxed
    bill.amount_tax = tax
    bill.amount_total = total
    bill.amount_residual = total
    bill.lines_json = json.dumps(out_lines)
    bill.notes = body.notes
    await db.flush()
    return await _bill_payload(db, bill)


@router.get("/bills/{bill_id}/pdf", response_class=HTMLResponse)
async def vendor_bill_pdf(
    bill_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    bill = await _load_vendor_bill(db, user, tenant_id, bill_id)
    payload = await _bill_payload(db, bill)
    company = await _tenant_company(db, tenant_id)
    return HTMLResponse(content=render_vendor_bill_html(company=company, bill=payload))


@router.post("/bills/{bill_id}/cancel")
async def cancel_vendor_bill(
    bill_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    bill = await _load_vendor_bill(db, user, tenant_id, bill_id)
    if bill.state != "draft":
        raise HTTPException(status_code=400, detail="Only draft bills can be cancelled")
    bill.state = "cancelled"
    await db.flush()
    return await _bill_payload(db, bill)


@router.post("/bills/{bill_id}/post")
async def confirm_vendor_bill(
    bill_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    bill = await _load_vendor_bill(db, user, tenant_id, bill_id)
    if bill.state == "posted":
        return await _bill_payload(db, bill)
    if not bill.lines_json or bill.lines_json == "[]":
        raise HTTPException(status_code=400, detail="Add at least one invoice line")
    try:
        await post_vendor_bill(db, bill)
    except AccPostingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await _bill_payload(db, bill)


@router.post("/bills/{bill_id}/pay")
async def pay_vendor_bill(
    bill_id: str,
    body: VendorPaymentIn,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    bill = await _load_vendor_bill(db, user, tenant_id, bill_id)
    await register_bill_payment(db, bill, body.amount, journal_code=body.journal_code, reference=body.reference)
    return await _bill_payload(db, bill)


def _quotation_payload(q: SaleQuotation) -> dict[str, Any]:
    qdate = q.quotation_date or (q.created_at.date() if q.created_at else None)
    return {
        "id": q.id,
        "name": q.name,
        "state": q.state,
        "customer_name": q.customer_name,
        "customer_id": q.customer_id,
        "validity_date": q.validity_date.isoformat() if q.validity_date else None,
        "quotation_date": qdate.isoformat() if qdate else None,
        "payment_terms": q.payment_terms or "immediate",
        "branch_id": q.branch_id,
        "amount_untaxed": q.amount_untaxed,
        "amount_tax": q.amount_tax,
        "amount_total": q.amount_total,
        "lines": json.loads(q.lines_json or "[]"),
        "terms": q.terms,
    }


def _compute_quotation_amounts(lines: list[QuotationLineIn]) -> tuple[float, float, float, list[dict]]:
    out_lines: list[dict] = []
    untaxed = 0.0
    tax = 0.0
    for ln in lines:
        sub = ln.quantity * ln.price_unit
        line_tax = sub * (ln.tax_rate / 100) if ln.tax_rate else 0
        untaxed += sub
        tax += line_tax
        pname = (ln.product_name or ln.label or "").strip() or "Product"
        out_lines.append(
            {
                "product_name": pname,
                "label": pname,
                "product_id": ln.product_id,
                "sku": ln.sku,
                "unit": ln.unit or "pcs",
                "quantity": ln.quantity,
                "price_unit": ln.price_unit,
                "tax_rate": ln.tax_rate,
                "amount": round(sub, 2),
            }
        )
    total = untaxed + tax
    return round(untaxed, 2), round(tax, 2), round(total, 2), out_lines


@router.get("/quotations")
async def list_quotations(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    branch_id: str | None = Query(None),
):
    tenant_id = require_tenant(user)
    q = select(SaleQuotation).where(SaleQuotation.tenant_id == tenant_id).order_by(SaleQuotation.created_at.desc())
    q = await _apply_doc_branch_filter(q, SaleQuotation.branch_id, user, db, tenant_id, branch_id)
    rows = (await db.execute(q.limit(100))).scalars().all()
    return [_quotation_payload(row) for row in rows]


@router.post("/quotations")
async def create_quotation(
    body: QuotationIn,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    untaxed, tax, total, lines_out = _compute_quotation_amounts(body.lines)
    count = (
        await db.scalar(select(func.count()).select_from(SaleQuotation).where(SaleQuotation.tenant_id == tenant_id))
        or 0
    )
    q_date = body.quotation_date or date.today()
    validity = body.validity_date or (q_date + timedelta(days=body.validity_days))
    record_branch = await resolve_sale_branch_id(db, user, tenant_id, body.branch_id)
    q = SaleQuotation(
        tenant_id=tenant_id,
        branch_id=record_branch,
        name=f"QT/{date.today().year}/{int(count) + 1:04d}",
        state="draft",
        customer_name=body.customer_name.strip(),
        customer_id=body.customer_id,
        validity_date=validity,
        quotation_date=q_date,
        payment_terms=(body.payment_terms or "immediate").strip()[:40],
        amount_untaxed=untaxed,
        amount_tax=tax,
        amount_total=total,
        lines_json=json.dumps(lines_out),
        terms=body.terms,
    )
    db.add(q)
    await db.flush()
    return _quotation_payload(q)


@router.get("/quotations/{quotation_id}")
async def get_quotation(
    quotation_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    branch_id: str | None = Query(None),
):
    tenant_id = require_tenant(user)
    q = await _load_quotation(db, user, tenant_id, quotation_id, branch_id=branch_id)
    return _quotation_payload(q)


@router.patch("/quotations/{quotation_id}")
async def update_quotation(
    quotation_id: str,
    body: QuotationIn,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    q = await _load_quotation(db, user, tenant_id, quotation_id, branch_id=body.branch_id)
    if q.state not in ("draft", "sent"):
        raise HTTPException(status_code=400, detail="Quotation cannot be edited in this state")
    untaxed, tax, total, lines_out = _compute_quotation_amounts(body.lines)
    q.customer_name = body.customer_name.strip()
    q.customer_id = body.customer_id
    if body.quotation_date:
        q.quotation_date = body.quotation_date
    if body.payment_terms:
        q.payment_terms = body.payment_terms.strip()[:40]
    if body.validity_date:
        q.validity_date = body.validity_date
    elif body.validity_days:
        base = q.quotation_date or date.today()
        q.validity_date = base + timedelta(days=body.validity_days)
    q.amount_untaxed = untaxed
    q.amount_tax = tax
    q.amount_total = total
    q.lines_json = json.dumps(lines_out)
    q.terms = body.terms
    await db.flush()
    return _quotation_payload(q)


@router.post("/quotations/{quotation_id}/confirm")
async def confirm_quotation(
    quotation_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    q = await _load_quotation(db, user, tenant_id, quotation_id)
    if q.state in ("cancel",):
        raise HTTPException(status_code=400, detail="Quotation is cancelled")
    q.state = "sale"
    await db.flush()
    return _quotation_payload(q)


@router.post("/quotations/{quotation_id}/cancel")
async def cancel_quotation(
    quotation_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    q = await _load_quotation(db, user, tenant_id, quotation_id)
    if q.state == "sale":
        raise HTTPException(status_code=400, detail="Confirmed quotations cannot be cancelled")
    q.state = "cancel"
    await db.flush()
    return _quotation_payload(q)


@router.post("/quotations/{quotation_id}/send")
async def send_quotation(
    quotation_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    q = await _load_quotation(db, user, tenant_id, quotation_id)
    q.state = "sent"
    await db.flush()
    return _quotation_payload(q)


@router.get("/quotations/{quotation_id}/pdf", response_class=HTMLResponse)
async def quotation_pdf(
    quotation_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    q = await _load_quotation(db, user, tenant_id, quotation_id)
    payload = _quotation_payload(q)
    company = await _tenant_company(db, tenant_id)
    return HTMLResponse(content=render_quotation_html(company=company, quotation=payload))


@router.get("/reports/run/{report_key}/xlsx")
async def accounting_run_report_xlsx(
    report_key: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    books_mode: str = Query("standard", pattern="^(standard|tra)$"),
    branch_id: str | None = Query(None),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    target_move: str = Query("posted", pattern="^(posted|all)$"),
):
    tenant_id = require_tenant(user)
    effective_branch = resolve_branch_filter(user, branch_id)
    payload = await run_odoo_report(
        db,
        report_key=report_key,
        tenant_id=tenant_id,
        branch_id=effective_branch,
        books_mode=books_mode,
        date_from=date_from,
        date_to=date_to,
        target_move=target_move,
    )
    company = await _tenant_company(db, tenant_id)
    try:
        data = report_payload_to_xlsx_bytes(report_key, payload, company["name"])
    except RuntimeError as e:
        raise HTTPException(status_code=501, detail=str(e)) from e
    filename = f"{report_key}.xlsx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/fiscal-positions")
async def get_fiscal_positions(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    rows = await ensure_default_fiscal_positions(db, tenant_id)
    return [
        {
            "id": r.id,
            "code": r.code,
            "name": r.name,
            "vat_output_account": r.vat_output_account,
            "vat_input_account": r.vat_input_account,
            "default_sale_tax_rate": r.default_sale_tax_rate,
        }
        for r in rows
    ]


@router.get("/customer-invoices")
async def api_list_customer_invoices(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    return await list_customer_invoices(db, tenant_id=tenant_id)


@router.get("/customer-invoices/{move_id}")
async def api_get_customer_invoice(
    move_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    detail = await get_customer_invoice_detail(db, tenant_id=tenant_id, move_id=move_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return detail


class CustomerPaymentIn(BaseModel):
    amount: float
    journal_code: str = "1000"
    reference: str = ""


@router.post("/customer-invoices/{move_id}/pay")
async def api_pay_customer_invoice(
    move_id: str,
    body: CustomerPaymentIn,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    inv = await get_customer_invoice_detail(db, tenant_id=tenant_id, move_id=move_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    await register_customer_payment_move(
        db,
        tenant_id=tenant_id,
        branch_id=None,
        move_id=move_id,
        partner_id=inv.get("partner_id"),
        partner_name=str(inv.get("partner_name") or ""),
        amount=body.amount,
        reference=body.reference or str(inv.get("name")),
        journal_code="CSH" if body.journal_code == "1000" else "BNK",
    )
    return await get_customer_invoice_detail(db, tenant_id=tenant_id, move_id=move_id)


@router.get("/bills/{bill_id}/purchase-matches")
async def api_bill_po_candidates(
    bill_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    bill = await _load_vendor_bill(db, user, tenant_id, bill_id)
    return {
        "candidates": await list_po_candidates(db, tenant_id=tenant_id, bill=bill),
        "matches": await list_matches_for_bill(db, tenant_id=tenant_id, bill_id=bill_id),
    }


class LinkPurchaseOrderIn(BaseModel):
    purchase_order_id: str
    matched_amount: float | None = None


@router.post("/bills/{bill_id}/link-purchase-order")
async def api_link_bill_po(
    bill_id: str,
    body: LinkPurchaseOrderIn,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    bill = await _load_vendor_bill(db, user, tenant_id, bill_id)
    try:
        match = await link_bill_to_po(
            db,
            tenant_id=tenant_id,
            bill=bill,
            purchase_order_id=body.purchase_order_id,
            matched_amount=body.matched_amount,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"match_id": match.id, "bill": await _bill_payload(db, bill)}


@router.get("/bank-statements")
async def api_list_bank_statements(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    return await list_statements(db, tenant_id=tenant_id)


class BankStatementIn(BaseModel):
    name: str = "Bank statement"
    date_from: date
    date_to: date
    balance_start: float = 0
    balance_end: float = 0


@router.post("/bank-statements")
async def api_create_bank_statement(
    body: BankStatementIn,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    st = await create_statement(
        db,
        tenant_id=tenant_id,
        name=body.name,
        date_from=body.date_from,
        date_to=body.date_to,
        balance_start=body.balance_start,
        balance_end=body.balance_end,
    )
    return {"id": st.id, "name": st.name}


@router.get("/bank-statements/{statement_id}/lines")
async def api_bank_statement_lines(
    statement_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    require_tenant(user)
    return await statement_lines(db, statement_id=statement_id)


class BankStatementLineIn(BaseModel):
    date: date
    payment_ref: str = ""
    partner_name: str = ""
    amount: float


@router.post("/bank-statements/{statement_id}/lines")
async def api_add_bank_line(
    statement_id: str,
    body: BankStatementLineIn,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    require_tenant(user)
    ln = await add_statement_line(
        db,
        statement_id=statement_id,
        line_date=body.date,
        payment_ref=body.payment_ref,
        partner_name=body.partner_name,
        amount=body.amount,
    )
    return {"id": ln.id}


class ReconcileIn(BaseModel):
    move_line_id: str


@router.post("/bank-statements/lines/{line_id}/reconcile")
async def api_reconcile_bank_line(
    line_id: str,
    body: ReconcileIn,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    try:
        await reconcile_line(db, tenant_id=tenant_id, statement_line_id=line_id, move_line_id=body.move_line_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True}


@router.get("/bank-reconciliation/suggest")
async def api_suggest_reconcile(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    amount: float = Query(...),
):
    tenant_id = require_tenant(user)
    return await suggest_move_lines(db, tenant_id=tenant_id, amount=amount)
