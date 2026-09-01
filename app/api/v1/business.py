import secrets

from datetime import UTC, datetime, timedelta

from typing import Annotated



from fastapi import APIRouter, Depends, HTTPException, Query, status

from sqlalchemy import func, select

from sqlalchemy.ext.asyncio import AsyncSession



from app.config import settings

from app.core.deps import get_current_user, get_user_permissions, require_permission, require_tenant, require_vendor_subscription

from app.core.ttl_cache import cache_get, cache_set, invalidate_tenant_cache, tenant_cache_key

from app.database import get_db

from app.models import Customer, Product, Sale, StockMovement, Supplier, User

from app.schemas import (

    CustomerCreate,

    CustomerResponse,

    CustomerUpdate,

    DashboardStats,

    PageMeta,

    PaginatedCustomers,

    PaginatedProducts,

    PaginatedSales,

    ProductCreate,

    ProductResponse,

    ProductUpdate,

    SaleCreate,

    SaleFinalize,

    SaleResponse,

    StockAdjustment,

    StockMovementResponse,

    SyncBatchRequest,

    SyncBatchResponse,

)

from app.services.transaction_service import create_sale_transaction, finalize_sale_transaction



router = APIRouter(tags=["business"], dependencies=[Depends(require_vendor_subscription)])





def _page_meta(total: int, skip: int, limit: int) -> PageMeta:

    return PageMeta(total=total, skip=skip, limit=limit, has_more=(skip + limit) < total)





async def _count(db: AsyncSession, q) -> int:

    count_stmt = select(func.count()).select_from(q.order_by(None).subquery())

    return int(await db.scalar(count_stmt) or 0)





# ── Dashboard ─────────────────────────────────────────────────────────────────



@router.get("/dashboard/stats", response_model=DashboardStats)

async def dashboard_stats(

    user: Annotated[User, Depends(get_current_user)],

    db: Annotated[AsyncSession, Depends(get_db)],

):

    tenant_id = require_tenant(user)

    cache_key = tenant_cache_key(tenant_id, "dashboard")

    cached = await cache_get(cache_key)

    if cached is not None:

        return DashboardStats(**{**cached, "cached": True})



    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)



    sales_today = await db.execute(

        select(func.coalesce(func.sum(Sale.total), 0), func.count(Sale.id))

        .where(Sale.tenant_id == tenant_id, Sale.created_at >= today)

    )

    today_revenue, today_sales_count = sales_today.one()



    product_count = await db.scalar(

        select(func.count(Product.id)).where(Product.tenant_id == tenant_id, Product.is_active == True)  # noqa: E712

    )

    low_stock = await db.scalar(

        select(func.count(Product.id)).where(

            Product.tenant_id == tenant_id,

            Product.is_active == True,  # noqa: E712

            Product.stock <= Product.reorder_point,

        )

    )

    expiring = await db.scalar(

        select(func.count(Product.id)).where(

            Product.tenant_id == tenant_id,

            Product.expiry_date.isnot(None),

            Product.expiry_date <= datetime.now(UTC) + timedelta(days=30),

        )

    )

    customer_count = await db.scalar(

        select(func.count(Customer.id)).where(Customer.tenant_id == tenant_id)

    )

    receivables = await db.scalar(

        select(func.coalesce(func.sum(Customer.balance), 0)).where(Customer.tenant_id == tenant_id)

    )

    payables = await db.scalar(

        select(func.coalesce(func.sum(Supplier.outstanding_payable), 0)).where(Supplier.tenant_id == tenant_id)

    )

    month_start = today.replace(day=1)

    monthly = await db.scalar(

        select(func.coalesce(func.sum(Sale.total), 0)).where(

            Sale.tenant_id == tenant_id, Sale.created_at >= month_start

        )

    )



    top_rows = await db.execute(

        select(Sale.items)

        .where(Sale.tenant_id == tenant_id, Sale.created_at >= month_start)

        .limit(200)

    )

    qty_by_product: dict[str, float] = {}
    rev_by_product: dict[str, float] = {}
    name_by_product: dict[str, str] = {}

    for (items,) in top_rows.all():
        for item in items or []:
            pid = str(item.get("product_id") or item.get("productId") or "")
            if not pid:
                continue
            qty = float(item.get("quantity") or 0)
            line_total = float(
                item.get("total")
                or item.get("total_price")
                or item.get("line_total")
                or 0
            )
            if line_total <= 0:
                unit = float(item.get("unit_price") or item.get("price") or 0)
                line_total = unit * qty
            qty_by_product[pid] = qty_by_product.get(pid, 0) + qty
            rev_by_product[pid] = rev_by_product.get(pid, 0) + line_total
            item_name = (item.get("product_name") or item.get("productName") or item.get("name") or "").strip()
            if item_name and pid not in name_by_product:
                name_by_product[pid] = item_name

    product_ids = [pid for pid in qty_by_product if pid]
    products_by_id: dict[str, Product] = {}
    if product_ids:
        prod_rows = await db.execute(
            select(Product).where(Product.tenant_id == tenant_id, Product.id.in_(product_ids))
        )
        products_by_id = {p.id: p for p in prod_rows.scalars().all()}

    top_products = []
    for pid, qty in sorted(qty_by_product.items(), key=lambda x: -x[1])[:5]:
        prod = products_by_id.get(pid)
        name = name_by_product.get(pid) or (prod.name if prod else "") or "Product"
        top_products.append({
            "product_id": pid,
            "name": name,
            "quantity": int(qty),
            "revenue": round(rev_by_product.get(pid, 0), 2),
        })



    payload = dict(

        today_revenue=float(today_revenue or 0),

        today_sales_count=int(today_sales_count or 0),

        total_products=int(product_count or 0),

        low_stock_count=int(low_stock or 0),

        expiring_soon_count=int(expiring or 0),

        total_customers=int(customer_count or 0),

        outstanding_receivables=float(receivables or 0),

        outstanding_payables=float(payables or 0),

        monthly_revenue=float(monthly or 0),

        top_products=top_products,

        cached=False,

    )

    await cache_set(cache_key, payload, settings.cache_ttl_seconds)

    return DashboardStats(**payload)





# ── Products ──────────────────────────────────────────────────────────────────



@router.get("/products", response_model=PaginatedProducts)

async def list_products(

    user: Annotated[User, Depends(get_current_user)],

    db: Annotated[AsyncSession, Depends(get_db)],

    search: str | None = None,

    category: str | None = None,

    low_stock: bool = False,

    expiring: bool = False,

    skip: int = Query(0, ge=0),

    limit: int = Query(100, ge=1, le=500),

):

    tenant_id = require_tenant(user)

    q = select(Product).where(Product.tenant_id == tenant_id, Product.is_active == True)  # noqa: E712

    if search:

        q = q.where(Product.name.ilike(f"%{search}%") | Product.sku.ilike(f"%{search}%"))

    if category:

        q = q.where(Product.category == category)

    if low_stock:

        q = q.where(Product.stock <= Product.reorder_point)

    if expiring:

        q = q.where(

            Product.expiry_date.isnot(None),

            Product.expiry_date <= datetime.now(UTC) + timedelta(days=30),

        )

    total = await _count(db, q)

    result = await db.execute(q.order_by(Product.name).offset(skip).limit(limit))

    items = result.scalars().all()

    return PaginatedProducts(items=items, meta=_page_meta(total, skip, limit))





@router.post("/products", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)

async def create_product(

    body: ProductCreate,

    user: Annotated[User, Depends(require_permission("canModifyInventory"))],

    db: Annotated[AsyncSession, Depends(get_db)],

):

    tenant_id = require_tenant(user)

    tenant = user.tenant

    product = Product(

        tenant_id=tenant_id,

        branch_id=body.branch_id,

        name=body.name,

        category=body.category,

        sku=body.sku,

        barcode=body.barcode,

        price=body.price,

        cost=body.cost,

        stock=body.stock,

        reorder_point=body.reorder_point,

        unit=body.unit,

        batch_number=body.batch_number,

        expiry_date=body.expiry_date,

        requires_prescription=body.requires_prescription,

        business_type=tenant.business_type if tenant else "retail",

        metadata_json=body.metadata_json,

    )

    db.add(product)

    await db.flush()

    await invalidate_tenant_cache(tenant_id)

    return product





@router.patch("/products/{product_id}", response_model=ProductResponse)

async def update_product(

    product_id: str,

    body: ProductUpdate,

    user: Annotated[User, Depends(require_permission("canModifyInventory"))],

    db: Annotated[AsyncSession, Depends(get_db)],

):

    tenant_id = require_tenant(user)

    result = await db.execute(

        select(Product).where(Product.id == product_id, Product.tenant_id == tenant_id)

    )

    product = result.scalar_one_or_none()

    if not product:

        raise HTTPException(status_code=404, detail="Product not found")

    for field, value in body.model_dump(exclude_unset=True).items():

        setattr(product, field, value)

    await db.flush()

    await invalidate_tenant_cache(tenant_id)

    return product





# ── Sales / POS ───────────────────────────────────────────────────────────────



@router.post("/sales", response_model=SaleResponse, status_code=status.HTTP_201_CREATED)

async def create_sale(

    body: SaleCreate,

    user: Annotated[User, Depends(require_permission("canSellPOS"))],

    db: Annotated[AsyncSession, Depends(get_db)],

):

    tenant_id = require_tenant(user)

    sale = await create_sale_transaction(db, body=body, user=user, tenant_id=tenant_id)

    await invalidate_tenant_cache(tenant_id)

    return sale





@router.patch("/sales/{sale_id}/finalize", response_model=SaleResponse)

async def finalize_sale(

    sale_id: str,

    body: SaleFinalize,

    user: Annotated[User, Depends(require_permission("canSellPOS"))],

    db: Annotated[AsyncSession, Depends(get_db)],

):

    tenant_id = require_tenant(user)

    result = await db.execute(

        select(Sale).where(Sale.id == sale_id, Sale.tenant_id == tenant_id)

    )

    sale = result.scalar_one_or_none()

    if not sale:

        raise HTTPException(status_code=404, detail="Sale not found")

    finalized = await finalize_sale_transaction(

        db,

        sale=sale,

        user=user,

        tenant_id=tenant_id,

        payments=body.payments,

        customer_id=body.customer_id,

        customer_name=body.customer_name,

    )

    await invalidate_tenant_cache(tenant_id)

    return finalized





@router.get("/sales", response_model=PaginatedSales)

async def list_sales(

    user: Annotated[User, Depends(get_current_user)],

    db: Annotated[AsyncSession, Depends(get_db)],

    skip: int = Query(0, ge=0),

    limit: int = Query(50, ge=1, le=500),

    status: str | None = Query(None, description="Filter by status, e.g. pending"),

):

    tenant_id = require_tenant(user)

    q = select(Sale).where(Sale.tenant_id == tenant_id)

    if status:

        if status == "pending":

            q = q.where(Sale.status.in_(["open", "pending_completion", "requires_attention", "ready_to_complete"]))

        else:

            q = q.where(Sale.status == status)

    total = await _count(db, q)

    result = await db.execute(q.order_by(Sale.created_at.desc()).offset(skip).limit(limit))

    items = result.scalars().all()

    return PaginatedSales(items=items, meta=_page_meta(total, skip, limit))





# ── Customers ─────────────────────────────────────────────────────────────────



@router.get("/customers", response_model=PaginatedCustomers)

async def list_customers(

    user: Annotated[User, Depends(get_current_user)],

    db: Annotated[AsyncSession, Depends(get_db)],

    search: str | None = None,

    skip: int = Query(0, ge=0),

    limit: int = Query(100, ge=1, le=500),

):

    tenant_id = require_tenant(user)

    q = select(Customer).where(Customer.tenant_id == tenant_id)

    if search:

        q = q.where(Customer.name.ilike(f"%{search}%") | Customer.phone.ilike(f"%{search}%"))

    total = await _count(db, q)

    result = await db.execute(q.order_by(Customer.name).offset(skip).limit(limit))

    items = result.scalars().all()

    return PaginatedCustomers(items=items, meta=_page_meta(total, skip, limit))





@router.post("/customers", response_model=CustomerResponse, status_code=status.HTTP_201_CREATED)

async def create_customer(

    body: CustomerCreate,

    user: Annotated[User, Depends(get_current_user)],

    db: Annotated[AsyncSession, Depends(get_db)],

):

    tenant_id = require_tenant(user)

    customer = Customer(tenant_id=tenant_id, **body.model_dump())

    db.add(customer)

    await db.flush()

    await invalidate_tenant_cache(tenant_id)

    return customer





@router.patch("/customers/{customer_id}", response_model=CustomerResponse)

async def update_customer(

    customer_id: str,

    body: CustomerUpdate,

    user: Annotated[User, Depends(get_current_user)],

    db: Annotated[AsyncSession, Depends(get_db)],

):

    tenant_id = require_tenant(user)

    result = await db.execute(

        select(Customer).where(Customer.id == customer_id, Customer.tenant_id == tenant_id)

    )

    customer = result.scalar_one_or_none()

    if not customer:

        raise HTTPException(status_code=404, detail="Customer not found")

    updates = body.model_dump(exclude_unset=True)

    if "balance" in updates:

        perms = get_user_permissions(user)

        if not (perms.get("canGiveCredit") or perms.get("canSellPOS")):

            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing permission to update customer balance")

    for field, value in updates.items():

        setattr(customer, field, value)

    await db.flush()

    await invalidate_tenant_cache(tenant_id)

    return customer





# ── Stock ─────────────────────────────────────────────────────────────────────



@router.post("/stock/adjust", response_model=StockMovementResponse)

async def adjust_stock(

    body: StockAdjustment,

    user: Annotated[User, Depends(require_permission("canModifyInventory"))],

    db: Annotated[AsyncSession, Depends(get_db)],

):

    tenant_id = require_tenant(user)

    result = await db.execute(

        select(Product).where(Product.id == body.product_id, Product.tenant_id == tenant_id)

    )

    product = result.scalar_one_or_none()

    if not product:

        raise HTTPException(status_code=404, detail="Product not found")



    prev = product.stock

    product.stock += body.quantity

    if product.stock < 0:

        raise HTTPException(status_code=400, detail="Stock cannot go negative")



    movement = StockMovement(

        tenant_id=tenant_id,

        product_id=product.id,

        product_name=product.name,

        sku=product.sku,

        movement_type=body.movement_type,

        quantity=body.quantity,

        previous_stock=prev,

        new_stock=product.stock,

        batch_number=body.batch_number,

        expiry_date=body.expiry_date,

        operator_name=user.name,

        notes=body.notes,

        client_id=body.client_id,

    )

    db.add(movement)

    await db.flush()

    await invalidate_tenant_cache(tenant_id)

    return movement





@router.get("/stock/movements", response_model=list[StockMovementResponse])

async def list_movements(

    user: Annotated[User, Depends(get_current_user)],

    db: Annotated[AsyncSession, Depends(get_db)],

    product_id: str | None = None,

    skip: int = Query(0, ge=0),

    limit: int = Query(100, ge=1, le=500),

):

    tenant_id = require_tenant(user)

    q = select(StockMovement).where(StockMovement.tenant_id == tenant_id)

    if product_id:

        q = q.where(StockMovement.product_id == product_id)

    result = await db.execute(q.order_by(StockMovement.created_at.desc()).offset(skip).limit(limit))

    return result.scalars().all()





# ── Offline Sync ──────────────────────────────────────────────────────────────



@router.post("/sync/batch", response_model=SyncBatchResponse)

async def sync_batch(

    body: SyncBatchRequest,

    user: Annotated[User, Depends(get_current_user)],

    db: Annotated[AsyncSession, Depends(get_db)],

):

    processed = 0

    failed = 0

    errors: list[str] = []



    for item in body.items:

        try:

            if item.entity_type == "sale" and item.action == "create":

                sale_data = SaleCreate(**item.payload)

                await create_sale_transaction(

                    db,

                    body=sale_data,

                    user=user,

                    tenant_id=require_tenant(user),

                )

                processed += 1

            elif item.entity_type == "stock" and item.action == "adjust":

                adj = StockAdjustment(**item.payload)

                tenant_id = require_tenant(user)

                pr = await db.execute(select(Product).where(Product.id == adj.product_id, Product.tenant_id == tenant_id))

                product = pr.scalar_one_or_none()

                if product:

                    prev = product.stock

                    product.stock += adj.quantity

                    db.add(StockMovement(

                        tenant_id=tenant_id, product_id=product.id, product_name=product.name,

                        sku=product.sku, movement_type=adj.movement_type, quantity=adj.quantity,

                        previous_stock=prev, new_stock=product.stock, operator_name=user.name,

                        notes=adj.notes, client_id=adj.client_id,

                    ))

                    processed += 1

                else:

                    failed += 1

                    errors.append(f"Product not found: {adj.product_id}")

            else:

                failed += 1

                errors.append(f"Unsupported: {item.entity_type}/{item.action}")

        except Exception as e:

            failed += 1

            errors.append(str(e))



    if processed:

        await invalidate_tenant_cache(require_tenant(user))



    return SyncBatchResponse(

        processed=processed,

        failed=failed,

        errors=errors,

        server_timestamp=datetime.now(UTC),

    )


