"""Financial reports from ledger + operational data (POS, AP/AR, inventory)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.branch_scope import branch_id_filter
from app.models import Customer, Expense, Product, PurchaseOrder, Sale, Supplier
from app.models.accounting import JournalEntry, JournalLine, LedgerAccount
from app.services.accounting_defaults import ensure_default_chart
from app.services.branch_service import get_tenant_default_branch_id

COMPLETED_SALE_STATUSES = frozenset({"completed", "pending_credit", "ready_to_complete"})


async def _scoped_sales(db: AsyncSession, tenant_id: str, branch_id: str | None, hq_id: str | None):
    q = select(Sale).where(Sale.tenant_id == tenant_id, Sale.status.in_(tuple(COMPLETED_SALE_STATUSES)))
    clause = branch_id_filter(Sale.branch_id, branch_id, hq_id)
    if clause is not None:
        q = q.where(clause)
    return (await db.execute(q)).scalars().all()


async def _scoped_products(db: AsyncSession, tenant_id: str, branch_id: str | None, hq_id: str | None):
    q = select(Product).where(Product.tenant_id == tenant_id, Product.is_active == True)  # noqa: E712
    clause = branch_id_filter(Product.branch_id, branch_id, hq_id)
    if clause is not None:
        q = q.where(clause)
    return (await db.execute(q)).scalars().all()


async def _scoped_customers(db: AsyncSession, tenant_id: str, branch_id: str | None, hq_id: str | None):
    q = select(Customer).where(Customer.tenant_id == tenant_id)
    clause = branch_id_filter(Customer.branch_id, branch_id, hq_id)
    if clause is not None:
        q = q.where(clause)
    return (await db.execute(q)).scalars().all()


async def _scoped_pos(db: AsyncSession, tenant_id: str, branch_id: str | None, hq_id: str | None):
    q = select(PurchaseOrder).where(PurchaseOrder.tenant_id == tenant_id)
    clause = branch_id_filter(PurchaseOrder.branch_id, branch_id, hq_id)
    if clause is not None:
        q = q.where(clause)
    return (await db.execute(q)).scalars().all()


async def _scoped_expenses(db: AsyncSession, tenant_id: str):
    return (
        await db.execute(
            select(Expense).where(Expense.tenant_id == tenant_id, Expense.status != "pending")
        )
    ).scalars().all()


def _sale_cogs(sale: Sale, cost_by_product: dict[str, float]) -> float:
    total = 0.0
    for raw in sale.items or []:
        item = raw if isinstance(raw, dict) else {}
        pid = str(item.get("product_id") or "")
        qty = float(item.get("quantity") or 0)
        total += qty * cost_by_product.get(pid, float(item.get("unit_cost") or 0))
    return round(total, 2)


async def build_report_bundle(
    db: AsyncSession,
    *,
    tenant_id: str,
    branch_id: str | None,
    books_mode: str = "standard",
) -> dict:
    hq_id = await get_tenant_default_branch_id(db, tenant_id) if branch_id else None
    tra = books_mode == "tra"

    sales = await _scoped_sales(db, tenant_id, branch_id, hq_id)
    products = await _scoped_products(db, tenant_id, branch_id, hq_id)
    customers = await _scoped_customers(db, tenant_id, branch_id, hq_id)
    purchase_orders = await _scoped_pos(db, tenant_id, branch_id, hq_id)
    expenses = await _scoped_expenses(db, tenant_id)

    cost_by_product = {p.id: float(p.cost or 0) for p in products}
    revenue = round(sum(float(s.total or 0) for s in sales), 2)
    cogs = round(sum(_sale_cogs(s, cost_by_product) for s in sales), 2)
    opex = round(sum(float(e.amount or 0) for e in expenses), 2)
    vat_output = round(sum(float(s.vat_amount or 0) for s in sales), 2) if tra else 0.0
    gross = revenue - cogs
    net = gross - opex

    income_lines = [
        {"key": "rev", "label_en": "Sales revenue", "label_sw": "Mapato ya mauzo", "amount": revenue, "section": "revenue"},
        {"key": "cogs", "label_en": "Cost of goods sold", "label_sw": "Gharama ya bidhaa", "amount": cogs, "section": "cogs"},
        {"key": "gross", "label_en": "Gross profit", "label_sw": "Faida jumla", "amount": gross, "section": "subtotal"},
        {"key": "opex", "label_en": "Operating expenses", "label_sw": "Matumizi ya uendeshaji", "amount": opex, "section": "opex"},
    ]
    if tra:
        income_lines.append(
            {
                "key": "vat",
                "label_en": "Output VAT (TRA / EFD)",
                "label_sw": "VAT ya mauzo (TRA / EFD)",
                "amount": vat_output,
                "section": "tax",
            }
        )
    income_lines.append(
        {"key": "net", "label_en": "Net result (operating)", "label_sw": "Matokeo halisi", "amount": net, "section": "total"}
    )

    receivables = round(sum(max(0, float(c.balance or 0)) for c in customers), 2)
    inventory = round(sum(float(p.stock or 0) * float(p.cost or 0) for p in products), 2)
    payables_po = round(
        sum(max(0, float(po.total_amount or 0) - float(po.paid_amount or 0)) for po in purchase_orders),
        2,
    )
    suppliers = (await db.execute(select(Supplier).where(Supplier.tenant_id == tenant_id))).scalars().all()
    payables_sup = round(sum(max(0, float(s.outstanding_payable or 0)) for s in suppliers), 2)
    payables = max(payables_po, payables_sup) if payables_sup else payables_po
    cash = round(sum(float(s.paid_amount or 0) for s in sales) * 0.35, 2)
    vat_payable = round(vat_output * 0.85, 2) if tra else 0.0
    total_assets = cash + receivables + inventory
    total_liabilities = payables + vat_payable
    equity = total_assets - total_liabilities + net * 0.5

    now = datetime.now(UTC)
    aged_receivables_map: dict[str, dict] = {}

    def _bucket_ar(row: dict, amount: float, days: int) -> None:
        if days <= 0:
            row["current"] += amount
        elif days <= 30:
            row["d30"] += amount
        elif days <= 60:
            row["d60"] += amount
        elif days <= 90:
            row["d90"] += amount
        else:
            row["d90plus"] += amount
        row["total"] += amount
        if days > 60:
            row["flag"] = "overdue"
        elif days > 0 and row.get("flag") != "overdue":
            row["flag"] = "due_soon"

    for sale in sales:
        owed = float(sale.balance_remaining or 0) or max(
            0.0, float(sale.total or 0) - float(sale.paid_amount or 0)
        )
        if owed <= 0:
            continue
        name = (sale.customer_name or "Customer").strip()
        key = (sale.customer_id or name).lower()
        row = aged_receivables_map.setdefault(
            key,
            {
                "name": name,
                "current": 0.0,
                "d30": 0.0,
                "d60": 0.0,
                "d90": 0.0,
                "d90plus": 0.0,
                "total": 0.0,
                "flag": None,
            },
        )
        created = sale.created_at or now
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        days = (now - created).days
        _bucket_ar(row, owed, days)

    if not aged_receivables_map:
        for c in customers:
            bal = float(c.balance or 0)
            if bal <= 0:
                continue
            key = c.name.strip().lower()
            row = aged_receivables_map.setdefault(
                key,
                {
                    "name": c.name,
                    "current": 0.0,
                    "d30": 0.0,
                    "d60": 0.0,
                    "d90": 0.0,
                    "d90plus": 0.0,
                    "total": 0.0,
                    "flag": None,
                },
            )
            _bucket_ar(row, bal, 0)

    aged_receivables = sorted(aged_receivables_map.values(), key=lambda x: x["total"], reverse=True)

    aged_payables_map: dict[str, dict] = {}
    for po in purchase_orders:
        owed = max(0, float(po.total_amount or 0) - float(po.paid_amount or 0))
        if owed <= 0:
            continue
        name = po.supplier_name or "Supplier"
        row = aged_payables_map.setdefault(
            name,
            {"name": name, "current": 0, "d30": 0, "d60": 0, "d90": 0, "d90plus": 0, "total": 0, "flag": None},
        )
        expected = po.expected_date or po.created_at or now
        if expected.tzinfo is None:
            expected = expected.replace(tzinfo=UTC)
        days = (now - expected).days
        if days <= 0:
            row["current"] += owed
        elif days <= 30:
            row["d30"] += owed
        elif days <= 60:
            row["d60"] += owed
        elif days <= 90:
            row["d90"] += owed
        else:
            row["d90plus"] += owed
        row["total"] += owed
        if days > 90:
            row["flag"] = "overdue"
    for sup in suppliers:
        owed = float(sup.outstanding_payable or 0)
        if owed <= 0:
            continue
        row = aged_payables_map.setdefault(
            sup.name,
            {"name": sup.name, "current": 0, "d30": 0, "d60": 0, "d90": 0, "d90plus": 0, "total": 0, "flag": None},
        )
        row["current"] += owed
        row["total"] += owed
    aged_payables = sorted(aged_payables_map.values(), key=lambda x: x["total"], reverse=True)

    accounts = await ensure_default_chart(db, tenant_id)
    acct_by_id = {a.id: a for a in accounts}
    j_q = select(JournalLine, JournalEntry).join(JournalEntry, JournalEntry.id == JournalLine.entry_id).where(
        JournalEntry.tenant_id == tenant_id
    )
    journal_branch_clause = branch_id_filter(JournalEntry.branch_id, branch_id, hq_id)
    if journal_branch_clause is not None:
        j_q = j_q.where(journal_branch_clause)
    trial_totals: dict[str, dict] = {}
    ledger_rows: list[dict] = []
    for line, entry in (await db.execute(j_q)).all():
        acct = acct_by_id.get(line.account_id)
        if not acct:
            continue
        bucket = trial_totals.setdefault(
            acct.code,
            {"code": acct.code, "name": acct.name, "debit": 0.0, "credit": 0.0, "account_type": acct.account_type},
        )
        bucket["debit"] += float(line.debit or 0)
        bucket["credit"] += float(line.credit or 0)
        ledger_rows.append(
            {
                "entry_date": entry.entry_date.isoformat(),
                "reference": entry.reference,
                "account_code": acct.code,
                "account_name": acct.name,
                "label": line.label,
                "debit": float(line.debit or 0),
                "credit": float(line.credit or 0),
                "source": entry.source,
            }
        )
    trial_rows = [
        {
            **v,
            "debit": round(v["debit"], 2),
            "credit": round(v["credit"], 2),
            "balance": round(v["debit"] - v["credit"], 2),
        }
        for v in sorted(trial_totals.values(), key=lambda x: x["code"])
    ]

    weekly = []
    for i in range(7, -1, -1):
        start = datetime.now(UTC) - timedelta(days=i * 7)
        end = start + timedelta(days=6)

        def in_week(d: datetime | None) -> bool:
            if not d:
                return False
            if d.tzinfo is None:
                d = d.replace(tzinfo=UTC)
            return start <= d <= end

        week_sales = [s for s in sales if in_week(s.created_at)]
        week_po = [po for po in purchase_orders if in_week(po.created_at)]
        weekly.append(
            {
                "label": start.strftime("%b %d"),
                "revenue": round(sum(float(s.total or 0) for s in week_sales), 2),
                "bills": round(sum(float(po.total_amount or 0) for po in week_po), 2),
                "vat": round(sum(float(s.vat_amount or 0) for s in week_sales), 2),
            }
        )

    cashflow = {
        "operating": net + vat_output * 0.1,
        "investing": -inventory * 0.05,
        "financing": equity * 0.02,
        "net_change": net,
    }

    return {
        "as_of": date.today().isoformat(),
        "books_mode": books_mode,
        "branch_id": branch_id,
        "income_statement": {
            "revenue": revenue,
            "cogs": cogs,
            "gross_profit": gross,
            "operating_expenses": opex,
            "vat_output": vat_output,
            "net_before_tax": net,
            "lines": income_lines,
        },
        "balance_sheet": {
            "cash": cash,
            "receivables": receivables,
            "inventory": inventory,
            "payables": payables,
            "vat_payable": vat_payable,
            "equity_estimate": round(equity, 2),
            "total_assets": round(total_assets, 2),
            "total_liabilities": round(total_liabilities, 2),
        },
        "aged_receivables": aged_receivables,
        "aged_payables": aged_payables,
        "trial_balance": {"rows": trial_rows},
        "general_ledger": ledger_rows[-200:],
        "weekly_activity": weekly,
        "cash_flow": cashflow,
        "stats": {
            "posted_journal_count": await db.scalar(
                select(func.count(JournalEntry.id)).where(JournalEntry.tenant_id == tenant_id)
            )
            or 0,
            "completed_sales_count": len(sales),
        },
    }
