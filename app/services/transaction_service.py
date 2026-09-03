"""Central sale/transaction orchestration — shared by POS, sync, and finalize flows."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Customer, Product, Sale, StockMovement, TenantSettings, User
from app.schemas import PaymentCreate, SaleCreate, SaleItemCreate
from app.core.branch_scope import assert_branch_record_access, get_staff_branch_id, resolve_sale_branch_id
from app.core.pricing_policy import validate_sale_pricing


COMPLETED_STATUSES = frozenset({"completed", "pending_credit"})
DRAFT_STATUSES = frozenset({"open", "pending_completion", "requires_attention", "ready_to_complete"})


def compute_sale_totals(items: list[SaleItemCreate], payments: list[PaymentCreate]) -> dict:
    subtotal = sum(item.total for item in items)
    vat = round(subtotal * settings.vat_rate, 2)
    total = subtotal + vat
    paid = sum(p.amount for p in payments)
    balance = max(0.0, total - paid)
    return {
        "subtotal": subtotal,
        "vat_amount": vat,
        "total": total,
        "paid_amount": paid,
        "balance_remaining": balance,
    }


def resolve_sale_status(*, finalize: bool, balance: float, has_items: bool) -> str:
    if not finalize:
        return "pending_completion" if has_items else "open"
    if balance > 0:
        return "pending_credit"
    return "completed"


async def find_sale_by_client_id(
    db: AsyncSession,
    tenant_id: str,
    client_id: str | None,
) -> Sale | None:
    if not client_id:
        return None
    result = await db.execute(
        select(Sale).where(Sale.tenant_id == tenant_id, Sale.client_id == client_id)
    )
    return result.scalar_one_or_none()


async def apply_stock_for_sale(
    db: AsyncSession,
    *,
    tenant_id: str,
    items: list[SaleItemCreate],
    receipt: str,
    operator_name: str,
    validate_stock: bool = True,
    branch_id: str | None = None,
) -> None:
    for item in items:
        result = await db.execute(
            select(Product).where(Product.id == item.product_id, Product.tenant_id == tenant_id)
        )
        product = result.scalar_one_or_none()
        if not product:
            raise HTTPException(status_code=404, detail=f"Product {item.product_id} not found")
        if branch_id and product.branch_id != branch_id:
            raise HTTPException(status_code=403, detail=f"Product {product.name} belongs to another branch")
        if validate_stock and product.stock < item.quantity:
            raise HTTPException(status_code=400, detail=f"Insufficient stock for {product.name}")
        prev = product.stock
        product.stock -= item.quantity
        db.add(
            StockMovement(
                tenant_id=tenant_id,
                product_id=product.id,
                product_name=product.name,
                sku=product.sku,
                movement_type="out_sale",
                quantity=-item.quantity,
                previous_stock=prev,
                new_stock=product.stock,
                reference_id=receipt,
                reference_type="SALE",
                operator_name=operator_name,
            )
        )


async def create_sale_transaction(
    db: AsyncSession,
    *,
    body: SaleCreate,
    user: User,
    tenant_id: str,
    finalize: bool | None = None,
) -> Sale:
    """Create or return existing sale (idempotent via client_id). Stock moves only when finalized."""
    should_finalize = body.finalize if finalize is None else finalize

    existing = await find_sale_by_client_id(db, tenant_id, body.client_id)
    if existing:
        return existing

    settings_row = await db.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
    )
    tenant_settings = settings_row.scalar_one_or_none()
    business_settings = tenant_settings.business_settings if tenant_settings else None
    await validate_sale_pricing(
        db,
        tenant_id=tenant_id,
        user=user,
        items=body.items,
        sale_type=body.sale_type,
        business_settings=business_settings,
    )

    totals = compute_sale_totals(body.items, body.payments)
    sale_branch_id = await resolve_sale_branch_id(db, user, tenant_id, body.branch_id)
    receipt = f"RCP-{datetime.now(UTC).strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}"
    status = resolve_sale_status(
        finalize=should_finalize,
        balance=totals["balance_remaining"],
        has_items=bool(body.items),
    )

    if should_finalize and body.items:
        await apply_stock_for_sale(
            db,
            tenant_id=tenant_id,
            items=body.items,
            receipt=receipt,
            operator_name=user.name,
            validate_stock=True,
            branch_id=sale_branch_id or get_staff_branch_id(user),
        )

        if body.customer_id and totals["balance_remaining"] > 0:
            cust_result = await db.execute(
                select(Customer).where(Customer.id == body.customer_id, Customer.tenant_id == tenant_id)
            )
            customer = cust_result.scalar_one_or_none()
            if customer:
                assert_branch_record_access(user, customer.branch_id, label="customer")
                customer.balance += totals["balance_remaining"]

    sale = Sale(
        tenant_id=tenant_id,
        branch_id=sale_branch_id,
        receipt_number=receipt,
        customer_id=body.customer_id,
        customer_name=body.customer_name,
        items=[i.model_dump() for i in body.items],
        subtotal=totals["subtotal"],
        vat_amount=totals["vat_amount"],
        total=totals["total"],
        paid_amount=totals["paid_amount"],
        balance_remaining=totals["balance_remaining"],
        payments=[p.model_dump() for p in body.payments],
        sale_type=body.sale_type,
        cashier_name=user.name,
        cashier_id=user.id,
        tra_efd_signature=f"TRA-EFD-{secrets.token_hex(8).upper()}" if should_finalize else None,
        status=status,
        client_id=body.client_id,
        synced=True,
    )
    db.add(sale)
    await db.flush()
    return sale


async def finalize_sale_transaction(
    db: AsyncSession,
    *,
    sale: Sale,
    user: User,
    tenant_id: str,
    payments: list[PaymentCreate] | None = None,
    customer_id: str | None = None,
    customer_name: str | None = None,
) -> Sale:
    """Complete a draft/pending sale — deduct stock once, update status."""
    if sale.status in COMPLETED_STATUSES:
        return sale

    if payments is not None:
        sale.payments = [p.model_dump() for p in payments]
    if customer_id is not None:
        sale.customer_id = customer_id
    if customer_name is not None:
        sale.customer_name = customer_name

    items = [SaleItemCreate(**i) for i in (sale.items or [])]
    payment_objs = [PaymentCreate(**p) for p in (sale.payments or [])]
    totals = compute_sale_totals(items, payment_objs)

    sale.subtotal = totals["subtotal"]
    sale.vat_amount = totals["vat_amount"]
    sale.total = totals["total"]
    sale.paid_amount = totals["paid_amount"]
    sale.balance_remaining = totals["balance_remaining"]

    if items:
        await apply_stock_for_sale(
            db,
            tenant_id=tenant_id,
            items=items,
            receipt=sale.receipt_number,
            operator_name=user.name,
            validate_stock=True,
        )

    if sale.customer_id and totals["balance_remaining"] > 0:
        cust_result = await db.execute(
            select(Customer).where(Customer.id == sale.customer_id, Customer.tenant_id == tenant_id)
        )
        customer = cust_result.scalar_one_or_none()
        if customer:
            customer.balance += totals["balance_remaining"]

    sale.status = "completed" if totals["balance_remaining"] == 0 else "pending_credit"
    if not sale.tra_efd_signature:
        sale.tra_efd_signature = f"TRA-EFD-{secrets.token_hex(8).upper()}"
    await db.flush()
    return sale
