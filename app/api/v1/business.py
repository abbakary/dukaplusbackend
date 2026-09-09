import secrets

from datetime import UTC, datetime, timedelta

from typing import Annotated



from fastapi import APIRouter, Depends, HTTPException, Query, status

from sqlalchemy import func, select

from sqlalchemy.ext.asyncio import AsyncSession



from app.config import settings

from app.core.branch_scope import (
    apply_customer_branch_filter,
    apply_product_branch_filter,
    apply_sale_branch_filter,
    assert_branch_record_access,
    branch_id_filter,
    get_staff_branch_id,
    load_customer_for_user,
    load_product_for_user,
    load_sale_for_user,
    resolve_branch_filter,
)
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
    _merge_product_metadata,
)

from app.services.analytics_service import build_analytics_snapshot
from app.services.branch_service import get_tenant_default_branch_id
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

    branch_id: str | None = Query(None, description="Filter by branch (owner only)"),

):

    tenant_id = require_tenant(user)

    effective_branch = resolve_branch_filter(user, branch_id)

    cache_key = tenant_cache_key(tenant_id, "dashboard", effective_branch or "all")

    cached = await cache_get(cache_key)

    if cached is not None:

        return DashboardStats(**{**cached, "cached": True})



    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)

    hq_branch_id = await get_tenant_default_branch_id(db, tenant_id) if effective_branch else None

    staff_branch = effective_branch

    sales_today_q = select(func.coalesce(func.sum(Sale.total), 0), func.count(Sale.id)).where(

        Sale.tenant_id == tenant_id, Sale.created_at >= today

    )

    sales_branch_clause = branch_id_filter(Sale.branch_id, staff_branch, hq_branch_id)

    if sales_branch_clause is not None:

        sales_today_q = sales_today_q.where(sales_branch_clause)

    sales_today = await db.execute(sales_today_q)

    today_revenue, today_sales_count = sales_today.one()



    product_count_q = select(func.count(Product.id)).where(Product.tenant_id == tenant_id, Product.is_active == True)  # noqa: E712

    product_branch_clause = branch_id_filter(Product.branch_id, staff_branch, hq_branch_id)

    if product_branch_clause is not None:

        product_count_q = product_count_q.where(product_branch_clause)

    product_count = await db.scalar(product_count_q)

    low_stock_q = select(func.count(Product.id)).where(

        Product.tenant_id == tenant_id,

        Product.is_active == True,  # noqa: E712

        Product.stock <= Product.reorder_point,

    )

    if product_branch_clause is not None:

        low_stock_q = low_stock_q.where(product_branch_clause)

    low_stock = await db.scalar(low_stock_q)

    expiring_q = select(func.count(Product.id)).where(

        Product.tenant_id == tenant_id,

        Product.expiry_date.isnot(None),

        Product.expiry_date <= datetime.now(UTC) + timedelta(days=30),

    )

    if product_branch_clause is not None:

        expiring_q = expiring_q.where(product_branch_clause)

    expiring = await db.scalar(expiring_q)

    customer_count_q = select(func.count(Customer.id)).where(Customer.tenant_id == tenant_id)

    customer_branch_clause = branch_id_filter(Customer.branch_id, staff_branch, hq_branch_id)

    if customer_branch_clause is not None:

        customer_count_q = customer_count_q.where(customer_branch_clause)

    customer_count = await db.scalar(customer_count_q)

    receivables_q = select(func.coalesce(func.sum(Customer.balance), 0)).where(Customer.tenant_id == tenant_id)

    if customer_branch_clause is not None:

        receivables_q = receivables_q.where(customer_branch_clause)

    receivables = await db.scalar(receivables_q)

    payables = 0.0 if staff_branch else await db.scalar(

        select(func.coalesce(func.sum(Supplier.outstanding_payable), 0)).where(Supplier.tenant_id == tenant_id)

    )

    month_start = today.replace(day=1)

    monthly_q = select(func.coalesce(func.sum(Sale.total), 0)).where(

        Sale.tenant_id == tenant_id, Sale.created_at >= month_start

    )

    if sales_branch_clause is not None:

        monthly_q = monthly_q.where(sales_branch_clause)

    monthly = await db.scalar(monthly_q)



    top_q = select(Sale.items).where(Sale.tenant_id == tenant_id, Sale.created_at >= month_start)

    if sales_branch_clause is not None:

        top_q = top_q.where(sales_branch_clause)

    top_rows = await db.execute(top_q.limit(200))

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

    stock_val_q = select(func.coalesce(func.sum(Product.stock * Product.cost), 0)).where(
        Product.tenant_id == tenant_id, Product.is_active == True  # noqa: E712
    )
    if product_branch_clause is not None:
        stock_val_q = stock_val_q.where(product_branch_clause)
    payload["stock_value"] = float(await db.scalar(stock_val_q) or 0)

    try:
        snapshot = await build_analytics_snapshot(db, tenant_id, "month")
        payload.update({
            "gross_sales": snapshot.get("gross_sales", payload["monthly_revenue"]),
            "cogs": snapshot.get("cogs", 0),
            "gross_margin": snapshot.get("gross_margin", 0),
            "total_opex": snapshot.get("total_opex", 0),
            "net_profit": snapshot.get("net_profit", 0),
        })
    except Exception:
        payload.update({
            "gross_sales": payload["monthly_revenue"],
            "cogs": 0,
            "gross_margin": payload["monthly_revenue"],
            "total_opex": 0,
            "net_profit": payload["monthly_revenue"],
        })

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

    branch_id: str | None = Query(None, description="Filter by branch (owner only)"),

):

    tenant_id = require_tenant(user)

    hq_branch_id = await get_tenant_default_branch_id(db, tenant_id)

    q = select(Product).where(Product.tenant_id == tenant_id, Product.is_active == True)  # noqa: E712

    q = apply_product_branch_filter(q, user, branch_id, hq_branch_id=hq_branch_id)

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

    auto_branch = get_staff_branch_id(user)
    if not auto_branch and not body.branch_id:
        auto_branch = await get_tenant_default_branch_id(db, tenant_id)

    product = Product(

        tenant_id=tenant_id,

        branch_id=body.branch_id or auto_branch,

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

    assert_branch_record_access(user, product.branch_id, label="product")

    data = body.model_dump(exclude_unset=True)
    meta_patch = data.pop("metadata_json", None)
    image_url = data.pop("image_url", None)
    vat_type = data.pop("vat_type", None)
    description = data.pop("description", None)
    location = data.pop("location", None)
    supplier = data.pop("supplier", None)

    for field, value in data.items():
        setattr(product, field, value)

    patch = dict(meta_patch or {})
    if description is not None:
        patch["description"] = description
    if location is not None:
        patch["location"] = location
    if supplier is not None:
        patch["supplier_name"] = supplier
    # Always merge metadata when any image/vat/meta fields touch the product
    if meta_patch is not None or image_url is not None or vat_type is not None or description is not None or location is not None or supplier is not None:
        clear_image = False
        if meta_patch is not None and "image_url" in meta_patch and meta_patch.get("image_url") in (None, ""):
            clear_image = True
        product.metadata_json = _merge_product_metadata(
            product.metadata_json if isinstance(product.metadata_json, dict) else {},
            patch,
            image_url=image_url,
            vat_type=vat_type,
            clear_image=clear_image,
        )

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

    sale = await load_sale_for_user(db, user, tenant_id, sale_id)

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

    branch_id: str | None = Query(None, description="Filter by branch (owner only)"),

):

    tenant_id = require_tenant(user)

    hq_branch_id = await get_tenant_default_branch_id(db, tenant_id)

    q = select(Sale).where(Sale.tenant_id == tenant_id)

    q = apply_sale_branch_filter(q, user, branch_id, hq_branch_id=hq_branch_id)

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

    branch_id: str | None = Query(None, description="Filter by branch (owner only)"),

):

    tenant_id = require_tenant(user)

    hq_branch_id = await get_tenant_default_branch_id(db, tenant_id)

    q = select(Customer).where(Customer.tenant_id == tenant_id)

    q = apply_customer_branch_filter(q, user, branch_id, hq_branch_id=hq_branch_id)

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

    branch_id = get_staff_branch_id(user)
    if not branch_id:
        branch_id = (
            body.branch_id if body.branch_id and body.branch_id not in ("all", "") else None
        )
    if not branch_id:
        branch_id = await get_tenant_default_branch_id(db, tenant_id)

    payload = body.model_dump(exclude={"branch_id"})

    customer = Customer(tenant_id=tenant_id, branch_id=branch_id, **payload)

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

    customer = await load_customer_for_user(db, user, tenant_id, customer_id)

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

    product = await load_product_for_user(db, user, tenant_id, body.product_id)



    prev = product.stock

    product.stock += body.quantity

    if product.stock < 0:

        raise HTTPException(status_code=400, detail="Stock cannot go negative")

    if body.unit_cost is not None and body.unit_cost > 0:
        product.cost = body.unit_cost

    if body.batch_number:
        product.batch_number = body.batch_number
    if body.expiry_date is not None:
        product.expiry_date = body.expiry_date

    # Preserve image/vat while attaching supplier on stock-in
    if body.supplier_id or body.supplier_name:
        product.metadata_json = _merge_product_metadata(
            product.metadata_json if isinstance(product.metadata_json, dict) else {},
            {
                **({"supplier_id": body.supplier_id} if body.supplier_id else {}),
                **({"supplier_name": body.supplier_name} if body.supplier_name else {}),
            },
        )

    note_parts = [body.notes or ""]
    if body.apply_vat and body.tax_amount:
        note_parts.append(f"VAT: {body.tax_amount}")
    if body.vat_note:
        note_parts.append(body.vat_note)
    notes = " — ".join(p for p in note_parts if p)

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

        notes=notes or None,

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

    staff_branch = get_staff_branch_id(user)

    q = select(StockMovement).where(StockMovement.tenant_id == tenant_id)

    if staff_branch:

        q = q.join(Product, Product.id == StockMovement.product_id).where(Product.branch_id == staff_branch)

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

            elif item.entity_type == "product" and item.action == "create":
                prod_data = ProductCreate(**item.payload)
                tenant_id = require_tenant(user)
                tenant = user.tenant
                auto_branch = get_staff_branch_id(user)
                if not auto_branch and not prod_data.branch_id:
                    auto_branch = await get_tenant_default_branch_id(db, tenant_id)
                product = Product(
                    tenant_id=tenant_id,
                    branch_id=prod_data.branch_id or auto_branch,
                    name=prod_data.name,
                    category=prod_data.category,
                    sku=prod_data.sku,
                    barcode=prod_data.barcode,
                    price=prod_data.price,
                    cost=prod_data.cost,
                    stock=prod_data.stock,
                    reorder_point=prod_data.reorder_point,
                    unit=prod_data.unit,
                    batch_number=prod_data.batch_number,
                    expiry_date=prod_data.expiry_date,
                    requires_prescription=prod_data.requires_prescription,
                    business_type=tenant.business_type if tenant else "retail",
                    metadata_json=prod_data.metadata_json,
                )
                db.add(product)
                await db.flush()
                processed += 1
            elif item.entity_type == "customer" and item.action == "create":
                cust_data = CustomerCreate(**item.payload)
                tenant_id = require_tenant(user)
                branch_id = get_staff_branch_id(user)
                if not branch_id:
                    branch_id = (
                        cust_data.branch_id
                        if cust_data.branch_id and cust_data.branch_id not in ("all", "")
                        else None
                    )
                if not branch_id:
                    branch_id = await get_tenant_default_branch_id(db, tenant_id)
                payload = cust_data.model_dump(exclude={"branch_id"})
                customer = Customer(tenant_id=tenant_id, branch_id=branch_id, **payload)
                db.add(customer)
                await db.flush()
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


