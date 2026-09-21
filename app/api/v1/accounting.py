from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.branch_scope import resolve_branch_filter
from app.core.deps import get_current_user, require_tenant, require_vendor_subscription
from app.database import get_db
from app.models import Sale, User
from app.models.accounting import JournalEntry, JournalLine, LedgerAccount
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
