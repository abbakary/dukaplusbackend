"""Server-side BI snapshot — one round-trip instead of loading full sales/products/expenses lists."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Expense, Product, Sale, Supplier


def _range_cutoff(range_key: str) -> datetime | None:
    now = datetime.now(UTC)
    if range_key == "all":
        return None
    if range_key == "year":
        return datetime(now.year, 1, 1, tzinfo=UTC)
    if range_key == "quarter":
        q_month = ((now.month - 1) // 3) * 3 + 1
        return datetime(now.year, q_month, 1, tzinfo=UTC)
    return datetime(now.year, now.month, 1, tzinfo=UTC)


def _month_label(month: int) -> str:
    labels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return labels[max(0, min(11, month - 1))]


async def build_analytics_snapshot(
    db: AsyncSession,
    tenant_id: str,
    range_key: str = "month",
) -> dict[str, Any]:
    cutoff = _range_cutoff(range_key)

    sales_q = select(Sale).where(Sale.tenant_id == tenant_id)
    if cutoff:
        sales_q = sales_q.where(Sale.created_at >= cutoff)
    sales_result = await db.execute(sales_q.order_by(Sale.created_at.desc()).limit(5000))
    sales = sales_result.scalars().all()

    products_result = await db.execute(
        select(Product).where(Product.tenant_id == tenant_id, Product.is_active == True)  # noqa: E712
    )
    products = products_result.scalars().all()
    cost_by_id = {p.id: float(p.cost or 0) for p in products}
    product_by_id = {p.id: p for p in products}

    exp_q = select(Expense).where(Expense.tenant_id == tenant_id)
    if cutoff:
        exp_q = exp_q.where(Expense.expense_date >= cutoff)
    expenses_result = await db.execute(exp_q.limit(3000))
    expenses = expenses_result.scalars().all()

    suppliers_result = await db.execute(select(Supplier).where(Supplier.tenant_id == tenant_id))
    suppliers = suppliers_result.scalars().all()

    gross_sales = sum(float(s.total or 0) for s in sales)
    cogs = 0.0
    product_qty: dict[str, float] = {}
    product_rev: dict[str, float] = {}
    category_profit: dict[str, float] = {}
    category_rev: dict[str, float] = {}

    for sale in sales:
        for item in sale.items or []:
            pid = item.get("product_id") or item.get("productId") or ""
            qty = float(item.get("quantity") or 0)
            total = float(item.get("total") or 0)
            cogs += cost_by_id.get(pid, 0) * qty
            product_qty[pid] = product_qty.get(pid, 0) + qty
            product_rev[pid] = product_rev.get(pid, 0) + total
            prod = product_by_id.get(pid)
            cat = prod.category if prod else "General"
            margin = total - cost_by_id.get(pid, 0) * qty
            category_profit[cat] = category_profit.get(cat, 0) + margin
            category_rev[cat] = category_rev.get(cat, 0) + total

    total_opex = sum(float(e.amount or 0) for e in expenses)
    gross_margin = gross_sales - cogs
    net_profit = gross_margin - total_opex

    mom_change = await _mom_change(db, tenant_id)

    cat_rows = []
    total_cat_profit = sum(category_profit.values()) or 1
    for cat, profit in sorted(category_profit.items(), key=lambda x: -x[1])[:12]:
        rev = category_rev.get(cat, 0)
        cat_rows.append({
            "category": cat,
            "profit": round(profit, 2),
            "margin_percent": round((profit / rev * 100) if rev else 0, 1),
            "profit_share_percent": round(profit / total_cat_profit * 100, 1),
        })

    monthly_pl = _monthly_pl(sales, products, expenses)

    top_products = []
    for pid, rev in sorted(product_rev.items(), key=lambda x: -x[1])[:8]:
        prod = product_by_id.get(pid)
        if not prod:
            continue
        qty = product_qty.get(pid, 0)
        cost = cost_by_id.get(pid, 0)
        gp = rev - cost * qty
        top_products.append({
            "product_id": pid,
            "product_name": prod.name,
            "category": prod.category,
            "unit_price": float(prod.price or 0),
            "cost_price": cost,
            "margin_percent": round((gp / rev * 100) if rev else 0, 1),
            "monthly_sales_volume": qty,
            "monthly_revenue": round(rev, 2),
            "monthly_gross_profit": round(gp, 2),
            "profit_contribution_percent": round(gp / (gross_margin or 1) * 100, 1),
            "pareto_class": "A" if rev > gross_sales * 0.1 else "B" if rev > gross_sales * 0.03 else "C",
            "stock_health_status": "ok" if prod.stock > prod.reorder_point else "low",
        })

    cost_savings = _cost_savings(top_products, products, expenses, suppliers)

    return {
        "range": range_key,
        "gross_sales": round(gross_sales, 2),
        "cogs": round(cogs, 2),
        "gross_margin": round(gross_margin, 2),
        "total_opex": round(total_opex, 2),
        "net_profit": round(net_profit, 2),
        "mom_change": mom_change,
        "category_profits": cat_rows,
        "monthly_pl": monthly_pl,
        "top_products": top_products,
        "cost_savings": cost_savings,
        "cached": False,
    }


async def _mom_change(db: AsyncSession, tenant_id: str) -> dict[str, Any]:
    now = datetime.now(UTC)
    this_start = datetime(now.year, now.month, 1, tzinfo=UTC)
    last_start = (this_start - timedelta(days=1)).replace(day=1)

    this_rev = await db.scalar(
        select(func.coalesce(func.sum(Sale.total), 0)).where(
            Sale.tenant_id == tenant_id, Sale.created_at >= this_start
        )
    )
    last_rev = await db.scalar(
        select(func.coalesce(func.sum(Sale.total), 0)).where(
            Sale.tenant_id == tenant_id,
            Sale.created_at >= last_start,
            Sale.created_at < this_start,
        )
    )
    this_rev = float(this_rev or 0)
    last_rev = float(last_rev or 0)
    if this_rev == 0 and last_rev == 0:
        return {"percent": 0, "has_data": False, "direction": "flat"}
    if last_rev == 0:
        return {"percent": 100, "has_data": True, "direction": "up"}
    pct = (this_rev - last_rev) / last_rev * 100
    return {
        "percent": round(abs(pct), 1),
        "has_data": True,
        "direction": "up" if pct > 0 else "down" if pct < 0 else "flat",
    }


def _monthly_pl(sales, products, expenses) -> list[dict[str, Any]]:
    cost_by_id = {p.id: float(p.cost or 0) for p in products}
    buckets: dict[str, dict[str, float]] = {}

    for sale in sales:
        key = sale.created_at.strftime("%Y-%m")
        b = buckets.setdefault(key, {"revenue": 0.0, "cogs": 0.0, "opex": 0.0})
        b["revenue"] += float(sale.total or 0)
        for item in sale.items or []:
            pid = item.get("product_id") or item.get("productId") or ""
            b["cogs"] += cost_by_id.get(pid, 0) * float(item.get("quantity") or 0)

    for exp in expenses:
        key = exp.expense_date.strftime("%Y-%m")
        b = buckets.setdefault(key, {"revenue": 0.0, "cogs": 0.0, "opex": 0.0})
        b["opex"] += float(exp.amount or 0)

    rows = []
    for key in sorted(buckets.keys())[-6:]:
        b = buckets[key]
        rev, cogs, opex = b["revenue"], b["cogs"], b["opex"]
        y, m = key.split("-")
        rows.append({
            "month": _month_label(int(m)),
            "revenue": round(rev, 2),
            "cogs": round(cogs, 2),
            "opex": round(opex, 2),
            "net_profit": round(rev - cogs - opex, 2),
        })
    return rows


def _cost_savings(top_products, products, expenses, suppliers) -> list[dict[str, Any]]:
    ops: list[dict[str, Any]] = []
    dead = [p for p in top_products if p.get("pareto_class") == "C" and p.get("monthly_sales_volume", 0) == 0]
    if dead:
        ops.append({
            "id": "dead-capital",
            "savings_label": "Review slow-moving SKUs",
            "tag": "Dead capital",
            "title": "Discount slow Class C SKUs",
            "description": f"{', '.join(p['product_name'] for p in dead[:2])} have weak turnover.",
        })

    utility = sum(
        float(e.amount or 0)
        for e in expenses
        if e.category in ("utilities_luku", "water")
    )
    if utility > 0:
        ops.append({
            "id": "utilities",
            "savings_label": f"Save ~TSh {int(utility * 0.1)} / mo",
            "tag": "Utilities",
            "title": "Trim electricity & water usage",
            "description": f"Current utility spend is TSh {int(utility)}.",
        })

    if suppliers:
        top = max(suppliers, key=lambda s: float(s.outstanding_payable or 0))
        if float(top.outstanding_payable or 0) > 0:
            ops.append({
                "id": "supplier-terms",
                "savings_label": f"Save ~TSh {int(float(top.outstanding_payable) * 0.05)}",
                "tag": "Suppliers",
                "title": "Negotiate early payment discount",
                "description": f"{top.name} has TSh {int(float(top.outstanding_payable))} payable.",
            })

    if not ops:
        ops.append({
            "id": "baseline",
            "savings_label": "Start recording data",
            "tag": "Guidance",
            "title": "Record sales, expenses, and suppliers",
            "description": "Once POS sales and shop expenses are recorded, opportunities appear here.",
        })
    return ops[:4]
