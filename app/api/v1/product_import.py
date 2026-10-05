"""Bulk product import from Excel — separate router so paths register reliably."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_permission, require_tenant, require_vendor_subscription
from app.core.ttl_cache import invalidate_tenant_cache
from app.database import get_db
from app.models import Product, User
from app.services.branch_service import get_tenant_default_branch_id
from app.core.branch_scope import get_staff_branch_id
from app.core.branch_scope import apply_product_branch_filter
from app.services.product_import_excel import (
    apply_product_import,
    build_import_template_xlsx,
    build_inventory_export_xlsx,
    parse_import_workbook,
)

router = APIRouter(tags=["business"], dependencies=[Depends(require_vendor_subscription)])


class ProductImportConfirmBody(BaseModel):
    rows: list[dict] = Field(default_factory=list)
    duplicate_mode: str = Field("skip", pattern="^(skip|update|create_new)$")
    branch_id: str | None = None
    # absolute: stock_qty is on-hand count (opening). add: stock_qty is received qty (like PO).
    stock_mode: str = Field("absolute", pattern="^(absolute|add)$")
    supplier: dict | None = None


@router.get("/products/import/template")
async def download_product_import_template(
    user: Annotated[User, Depends(require_permission("canModifyInventory"))],
):
    tenant = user.tenant
    btype = tenant.business_type.value if tenant and tenant.business_type else "retail"
    try:
        data = build_import_template_xlsx(business_type=btype, include_sample=True)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="DukaPlus_RFQ_Inventory_Import.xlsx"'},
    )


@router.get("/products/import/export")
async def export_product_inventory_excel(
    user: Annotated[User, Depends(require_permission("canModifyInventory"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    branch_id: str | None = None,
):
    """Download current inventory in RFQ import template (edit & re-import)."""
    tenant_id = require_tenant(user)
    tenant = user.tenant
    btype = tenant.business_type.value if tenant and tenant.business_type else "retail"
    bid = branch_id or get_staff_branch_id(user)
    hq = await get_tenant_default_branch_id(db, tenant_id) if bid else None
    q = select(Product).where(Product.tenant_id == tenant_id, Product.is_active == True)  # noqa: E712
    q = apply_product_branch_filter(q, user, bid, hq_branch_id=hq)
    rows = (await db.execute(q.order_by(Product.name))).scalars().all()
    payload = []
    for p in rows:
        meta = p.metadata_json if isinstance(p.metadata_json, dict) else {}
        payload.append(
            {
                "name": p.name,
                "sku": p.sku,
                "stock": p.stock,
                "cost": p.cost,
                "price": p.price,
                "unit": p.unit,
                "barcode": p.barcode,
                "category": p.category,
                "batch_number": p.batch_number,
                "expiry_date": p.expiry_date,
                "reorder_point": p.reorder_point,
                "metadata_json": meta,
            }
        )
    try:
        data = build_inventory_export_xlsx(payload, business_type=btype)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="DukaPlus_Inventory_Export.xlsx"'},
    )


@router.post("/products/import/preview")
async def preview_product_import(
    user: Annotated[User, Depends(require_permission("canModifyInventory"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    file: UploadFile = File(...),
    stock_mode: str = Form("absolute"),
):
    tenant_id = require_tenant(user)
    raw = await file.read()
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 8MB)")
    existing_rows = (
        await db.execute(
            select(Product.sku, Product.barcode, Product.stock, Product.cost).where(
                Product.tenant_id == tenant_id, Product.is_active == True  # noqa: E712
            )
        )
    ).all()
    skus = {str(r[0]).lower() for r in existing_rows if r[0]}
    barcodes = {str(r[1]) for r in existing_rows if r[1]}
    existing_by_sku = {
        str(r[0]).lower(): {"stock": float(r[2] or 0), "cost": float(r[3] or 0)}
        for r in existing_rows
        if r[0]
    }
    try:
        sm = stock_mode if stock_mode in ("absolute", "add") else "absolute"
        preview = parse_import_workbook(
            raw,
            existing_skus=skus,
            existing_barcodes=barcodes,
            existing_by_sku=existing_by_sku,
            stock_mode=sm,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read Excel file: {e}") from e
    return preview


@router.post("/products/import/confirm")
async def confirm_product_import(
    body: ProductImportConfirmBody,
    user: Annotated[User, Depends(require_permission("canModifyInventory"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    tenant = user.tenant
    btype = tenant.business_type.value if tenant and tenant.business_type else "retail"
    branch_id = body.branch_id
    if not branch_id:
        branch_id = get_staff_branch_id(user) or await get_tenant_default_branch_id(db, tenant_id)
    items = [r for r in body.rows if r.get("status") != "error"]
    if not items:
        raise HTTPException(status_code=400, detail="No valid rows to import")
    dup_mode = "create_new" if body.duplicate_mode == "create_new" else body.duplicate_mode
    stock_mode = body.stock_mode if body.stock_mode in ("absolute", "add") else "absolute"
    supplier_info = body.supplier
    if not supplier_info and items:
        supplier_info = (items[0].get("supplier_block") or None) if isinstance(items[0], dict) else None
    result = await apply_product_import(
        db,
        tenant_id=tenant_id,
        branch_id=branch_id,
        business_type=btype,
        items=items,
        duplicate_mode=dup_mode,
        stock_mode=stock_mode,
        operator_name=user.name or "Excel import",
        supplier_info=supplier_info,
    )
    await invalidate_tenant_cache(tenant_id)
    return result
