import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, require_tenant, require_vendor_subscription
from app.database import get_db
from app.models import User
from app.models.tra_efd import FiscalReceiptRecord, TenantTraEfdConfig
from app.services.tra_efd_service import (
    DEFAULT_API_BASE,
    apply_client_secret,
    build_demo_tra_receipt_parsed,
    build_generatereceipt_payload,
    compute_sale_fiscal_totals,
    config_to_public_dict,
    fiscal_record_to_dict,
    parse_tra_receipt_response,
    post_generatereceipt,
    test_tra_connection,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tenant", tags=["tra-efd"], dependencies=[Depends(require_vendor_subscription)])


class TraEfdConfigUpdate(BaseModel):
    active: bool | None = None
    api_base_url: str | None = None
    client_id: str | None = None
    client_secret: str | None = Field(None, description="Sent once; stored encrypted server-side")
    company_city: str | None = None
    company_mobile: str | None = None
    default_id_type: str | None = None
    is_demo: bool | None = None


class GenerateReceiptBody(BaseModel):
    sale: dict[str, Any]
    customer: dict[str, Any] | None = None
    vat_rate: float = 0.18
    payment_type: str = "CASH"
    branch_id: str | None = None


async def _get_or_create_config(db: AsyncSession, tenant_id: str) -> TenantTraEfdConfig:
    result = await db.execute(select(TenantTraEfdConfig).where(TenantTraEfdConfig.tenant_id == tenant_id))
    row = result.scalar_one_or_none()
    if row:
        return row
    row = TenantTraEfdConfig(tenant_id=tenant_id, api_base_url=DEFAULT_API_BASE, active=False)
    db.add(row)
    await db.flush()
    return row


@router.get("/tra-efd/config")
async def get_tra_efd_config(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    config = await _get_or_create_config(db, tenant_id)
    return config_to_public_dict(config)


@router.put("/tra-efd/config")
async def update_tra_efd_config(
    body: TraEfdConfigUpdate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    config = await _get_or_create_config(db, tenant_id)
    if body.active is not None:
        config.active = body.active
    if body.api_base_url is not None:
        config.api_base_url = body.api_base_url.strip() or DEFAULT_API_BASE
    if body.client_id is not None:
        config.client_id = body.client_id.strip()
    if body.company_city is not None:
        config.company_city = body.company_city.strip()
    if body.company_mobile is not None:
        config.company_mobile = body.company_mobile.strip()
    if body.default_id_type is not None:
        config.default_id_type = body.default_id_type.strip()[:2]
    if body.is_demo is not None:
        config.is_demo = body.is_demo
    apply_client_secret(config, body.client_secret)
    await db.flush()
    return config_to_public_dict(config)


@router.post("/tra-efd/test-connection")
async def tra_efd_test_connection(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    config = await _get_or_create_config(db, tenant_id)
    ok, message = await test_tra_connection(config)
    await db.flush()
    return {"ok": ok, "message": message, "config": config_to_public_dict(config)}


@router.post("/tra-efd/generate-receipt")
async def tra_efd_generate_receipt(
    body: GenerateReceiptBody,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = require_tenant(user)
    config = await _get_or_create_config(db, tenant_id)
    if not config.active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="TRA fiscal integration is not enabled.")

    sale = body.sale or {}
    payload = build_generatereceipt_payload(body.model_dump(), config)
    excl, tax, incl = compute_sale_fiscal_totals(sale, body.vat_rate)

    raw: dict[str, Any] | Any
    if config.is_demo:
        parsed = build_demo_tra_receipt_parsed(config, sale, body.vat_rate)
        raw = {"source": "server_demo_efd", "demo": True, **{k: parsed.get(k) for k in parsed if k != "raw"}}
    else:
        try:
            raw = await post_generatereceipt(config, payload)
        except Exception as exc:
            logger.exception("TRA generate-receipt request failed")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"TRA fiscal service error: {exc}",
            ) from exc
        parsed = parse_tra_receipt_response(raw if isinstance(raw, dict) else {})
        if parsed.get("status") != "success" and not parsed.get("verification_code"):
            err = ""
            if isinstance(raw, dict):
                err = str(raw.get("message") or raw.get("MSG") or raw.get("error") or "")
            return {
                "ok": False,
                "status": "failed",
                "error": err or "TRA did not return a verification code.",
                "raw": raw,
            }

    customer_name = str(
        (body.customer or {}).get("name") or sale.get("customer_name") or sale.get("customerName") or "Walk-in Customer"
    )
    record = FiscalReceiptRecord(
        tenant_id=tenant_id,
        branch_id=body.branch_id,
        sale_id=str(sale.get("id") or "") or None,
        invoice_reference=str(sale.get("receipt_number") or sale.get("receiptNumber") or sale.get("id") or ""),
        receipt_number=parsed.get("receipt_number") or "",
        verification_code=parsed.get("verification_code") or "",
        verify_link=parsed.get("verify_link") or "",
        z_number=parsed.get("z_number") or config.company_serial or "",
        vrn=parsed.get("vrn") or config.company_vrn or "",
        status="demo" if config.is_demo else (parsed.get("status") or "failed"),
        is_demo=config.is_demo,
        customer_name=customer_name,
        total_excl_tax=float(parsed.get("total_excl_tax") or excl),
        total_tax=float(parsed.get("total_tax") or tax),
        total_incl_tax=float(parsed.get("total_incl_tax") or incl),
        items_json=payload.get("items") or [],
        api_response_raw=str(raw)[:8000],
        error_message="" if parsed.get("status") == "success" or config.is_demo else str(
            (raw.get("message") if isinstance(raw, dict) else "") or (raw.get("MSG") if isinstance(raw, dict) else "")
        ),
    )
    db.add(record)
    await db.flush()

    if parsed.get("status") != "success" and not parsed.get("verification_code"):
        err_msg = record.error_message
        if not err_msg and isinstance(raw, dict):
            err_msg = str(raw.get("message") or raw.get("MSG") or "")
        return {
            "ok": False,
            "status": "failed",
            "error": err_msg or "TRA did not return a verification code.",
            "receipt": fiscal_record_to_dict(record),
            "raw": raw,
        }

    return {
        "ok": True,
        "status": "success" if not config.is_demo else "demo",
        "receipt_number": record.receipt_number,
        "verification_code": record.verification_code,
        "verification_link": record.verify_link,
        "verify_link": record.verify_link,
        "z_number": record.z_number,
        "vrn": record.vrn,
        "receipt": fiscal_record_to_dict(record),
        "raw": raw,
    }


@router.get("/tra-efd/receipts")
async def list_tra_efd_receipts(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(50, ge=1, le=200),
):
    tenant_id = require_tenant(user)
    result = await db.execute(
        select(FiscalReceiptRecord)
        .where(FiscalReceiptRecord.tenant_id == tenant_id)
        .order_by(FiscalReceiptRecord.created_at.desc())
        .limit(limit)
    )
    rows = result.scalars().all()
    return [fiscal_record_to_dict(r) for r in rows]
