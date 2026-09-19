"""Server-side TRA VEFD client — credentials never exposed to browsers."""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.secret_box import decrypt_secret, encrypt_secret
from app.models.tra_efd import FiscalReceiptRecord, TenantTraEfdConfig

logger = logging.getLogger(__name__)

DEFAULT_API_BASE = "https://webshop.co.tz/api/public/index.php/api/v1"

# { tenant_id: {"token": str, "expires_at": float} }
_TOKEN_CACHE: dict[str, dict[str, Any]] = {}


def _unwrap_api_response(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    for key in ("data", "receipt", "result"):
        wrapped = data.get(key)
        if isinstance(wrapped, dict):
            return wrapped
    return data


def _response_value(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        val = data.get(key)
        if val not in (None, False, ""):
            return val
    return None


def normalise_tra_status(data: dict[str, Any], receipt_number: str, verification_code: str, verify_link: str) -> str:
    status = str(data.get("status") or data.get("success") or "").lower()
    if status in ("success", "true", "1", "ok", "approved"):
        return "success"
    if status in ("failed", "false", "0", "error", "rejected"):
        return "failed"
    if receipt_number and verification_code and verify_link:
        return "success"
    if verification_code:
        return "success"
    return "failed"


def parse_tra_receipt_response(raw: dict[str, Any]) -> dict[str, Any]:
    inner = _unwrap_api_response(raw)
    verify_link = str(
        _response_value(
            inner,
            "url_link",
            "urlLink",
            "url",
            "link",
            "verif_link",
            "verify_link",
            "verification_link",
            "verification_url",
            "qr_code_url",
        )
        or ""
    )
    verification_code = str(
        _response_value(
            inner,
            "verification_code",
            "verificationCode",
            "verificationcode",
            "verify_code",
            "verification",
            "code",
            "RCTVNUM",
            "rctvnum",
        )
        or ""
    )
    receipt_number = str(
        _response_value(
            inner,
            "receipt_number",
            "receiptNumber",
            "receipt_no",
            "receiptno",
            "receipt_num",
            "znum",
        )
        or ""
    )
    status = normalise_tra_status(inner, receipt_number, verification_code, verify_link)
    return {
        "status": status,
        "receipt_number": receipt_number,
        "verification_code": verification_code,
        "verify_link": verify_link,
        "z_number": str(_response_value(inner, "znum", "z_number", "zNumber") or ""),
        "vrn": str(_response_value(inner, "vrn") or ""),
        "total_excl_tax": float(
            _response_value(inner, "total_excl_of_tax", "total_excl_tax", "net_total", "subtotal") or 0
        ),
        "total_tax": float(_response_value(inner, "total_tax", "tax_total", "vat_total") or 0),
        "total_incl_tax": float(
            _response_value(inner, "total_incl_of_tax", "total_incl_tax", "gross_total", "total") or 0
        ),
        "raw": raw,
    }


def map_customer_id_type(label: str | None, default: str = "6") -> str:
    if not label:
        return default
    key = label.strip().lower()
    mapping = {
        "tin": "1",
        "1": "1",
        "driving license": "2",
        "driving_license": "2",
        "2": "2",
        "voters": "3",
        "3": "3",
        "passport": "4",
        "4": "4",
        "nida": "5",
        "5": "5",
        "telephone": "6",
        "none": "6",
        "6": "6",
    }
    return mapping.get(key, default)


def build_generatereceipt_payload(body: dict[str, Any], config: TenantTraEfdConfig) -> dict[str, Any]:
    sale = body.get("sale") or {}
    customer = body.get("customer") or {}
    vat_rate = float(body.get("vat_rate") or 0.18)
    items_in = sale.get("items") or []
    items_out: list[dict[str, Any]] = []

    for line in items_in:
        qty = float(line.get("quantity") or 0)
        unit = float(line.get("unit_price") or line.get("unitPrice") or 0)
        line_total = float(line.get("total") or line.get("totalPrice") or qty * unit)
        discount = float(line.get("discount_amount") or line.get("discountPercent") or 0)
        if vat_rate > 0:
            net = round(line_total / (1 + vat_rate), 2)
            tax_amount = round(line_total - net, 2)
            tax_code = 1
        else:
            net = round(line_total, 2)
            tax_amount = 0.0
            tax_code = 3
        items_out.append(
            {
                "itemcode": str(line.get("sku") or line.get("product_id") or line.get("productId") or "ITEM")[:32],
                "itemdesc": str(line.get("product_name") or line.get("productName") or "Item")[:200],
                "itemqty": qty,
                "net": net,
                "tax": tax_amount,
                "amount": round(line_total, 2),
                "discountamout": round(discount, 2),
                "itemtaxcode": int(line.get("itemtaxcode") or tax_code),
            }
        )

    id_type = map_customer_id_type(
        str(customer.get("id_type") or customer.get("idType") or ""),
        config.default_id_type or "6",
    )
    id_number = str(customer.get("id_number") or customer.get("idNumber") or "").replace("-", "").replace(" ", "")[:9]
    mobile = str(customer.get("mobile") or "")
    if mobile:
        mobile = mobile.replace("+255", "0").replace(" ", "")[:10]

    invoice_ref = str(sale.get("receipt_number") or sale.get("receiptNumber") or sale.get("id") or "")
    invoice_date = str(sale.get("date") or "")[:10]
    if not invoice_date or len(invoice_date) < 10:
        invoice_date = datetime.now(UTC).date().isoformat()

    return {
        "dbrecord": {
            "invoice_id": invoice_ref,
            "invoice_date": invoice_date,
        },
        "customer": {
            "idtype": id_type,
            "idnumber": id_number,
            "mobile": mobile,
            "name": str(customer.get("name") or sale.get("customer_name") or sale.get("customerName") or "Walk-in Customer"),
        },
        "items": items_out,
        "payment": {"paymenttype": str(body.get("payment_type") or "CASH").upper()},
    }


async def _login(config: TenantTraEfdConfig) -> str:
    secret = decrypt_secret(config.client_secret_enc)
    if not config.client_id or not secret:
        raise ValueError("TRA client ID and secret are required.")

    base = (config.api_base_url or DEFAULT_API_BASE).rstrip("/")
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{base}/login",
            data={"client_id": config.client_id, "client_secret": secret},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        data = resp.json()

    token = data.get("access_token")
    if not token:
        raise ValueError("TRA login did not return an access token.")

    expires_in = int(data.get("expires_in") or 3600)
    _TOKEN_CACHE[config.tenant_id] = {
        "token": token,
        "expires_at": time.time() + expires_in - 60,
        "name": data.get("name") or "",
        "email": data.get("email") or "",
    }
    return str(token)


async def get_bearer_token(config: TenantTraEfdConfig, force_refresh: bool = False) -> str:
    cached = _TOKEN_CACHE.get(config.tenant_id)
    now = time.time()
    if not force_refresh and cached and now < float(cached.get("expires_at") or 0):
        return str(cached["token"])
    return await _login(config)


async def sync_company_profile(config: TenantTraEfdConfig) -> dict[str, Any]:
    token = await get_bearer_token(config)
    base = (config.api_base_url or DEFAULT_API_BASE).rstrip("/")
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{base}/companyprofile",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()

    config.company_vrn = str(data.get("vrn") or "")
    config.company_tin = str(data.get("tin") or "")
    config.company_serial = str(data.get("serial") or "")
    config.company_vin = str(data.get("vin") or "")
    config.tax_office = str(data.get("taxoffice") or data.get("tax_office") or "")
    cached = _TOKEN_CACHE.get(config.tenant_id) or {}
    config.token_user_name = str(cached.get("name") or "")
    config.token_email = str(cached.get("email") or "")
    return data


async def test_tra_connection(config: TenantTraEfdConfig) -> tuple[bool, str]:
    try:
        await get_bearer_token(config, force_refresh=True)
        await sync_company_profile(config)
        config.connection_status = "connected"
        config.last_connection = datetime.now(UTC)
        return True, f"Connected as {config.token_user_name or 'TRA user'} ({config.token_email or '—'})"
    except Exception as exc:
        logger.warning("TRA connection test failed: %s", exc)
        config.connection_status = "failed"
        return False, str(exc)


async def post_generatereceipt(
    config: TenantTraEfdConfig,
    payload: dict[str, Any],
) -> dict[str, Any]:
    base = (config.api_base_url or DEFAULT_API_BASE).rstrip("/")
    token = await get_bearer_token(config)

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{base}/generatereceipt",
            json=payload,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )

        if resp.status_code == 401:
            _TOKEN_CACHE.pop(config.tenant_id, None)
            token = await get_bearer_token(config, force_refresh=True)
            resp = await client.post(
                f"{base}/generatereceipt",
                json=payload,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )

        try:
            data = resp.json()
        except ValueError:
            return {
                "status": "failed",
                "error": f"TRA returned non-JSON (HTTP {resp.status_code})",
                "raw_text": resp.text[:2000],
            }

        if resp.status_code >= 400:
            data.setdefault("status", "error")
            data.setdefault("message", f"HTTP {resp.status_code}")
        return data


def apply_client_secret(config: TenantTraEfdConfig, client_secret: str | None) -> None:
    if client_secret is None:
        return
    secret = client_secret.strip()
    if secret:
        config.client_secret_enc = encrypt_secret(secret)


def config_to_public_dict(config: TenantTraEfdConfig) -> dict[str, Any]:
    return {
        "active": config.active,
        "enabled": config.active,
        "api_base_url": config.api_base_url or DEFAULT_API_BASE,
        "client_id": config.client_id or "",
        "has_client_secret": bool(config.client_secret_enc),
        "company_city": config.company_city or "",
        "company_mobile": config.company_mobile or "",
        "default_id_type": config.default_id_type or "6",
        "is_demo": config.is_demo,
        "demo_mode": config.is_demo,
        "connection_status": config.connection_status or "not_tested",
        "last_connection": config.last_connection.isoformat() if config.last_connection else None,
        "company_vrn": config.company_vrn or "",
        "company_tin": config.company_tin or "",
        "company_serial": config.company_serial or "",
        "company_vin": config.company_vin or "",
        "tax_office": config.tax_office or "",
        "token_user_name": config.token_user_name or "",
        "token_email": config.token_email or "",
    }


def fiscal_record_to_dict(row: FiscalReceiptRecord) -> dict[str, Any]:
    return {
        "id": row.id,
        "sale_id": row.sale_id,
        "invoice_reference": row.invoice_reference,
        "receipt_number": row.receipt_number,
        "verification_code": row.verification_code,
        "verify_link": row.verify_link,
        "z_number": row.z_number,
        "vrn": row.vrn,
        "status": row.status,
        "is_demo": row.is_demo,
        "customer_name": row.customer_name,
        "total_excl_tax": row.total_excl_tax,
        "total_tax": row.total_tax,
        "total_incl_tax": row.total_incl_tax,
        "items": row.items_json or [],
        "error_message": row.error_message,
        "branch_id": row.branch_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
