"""ClickPesa Collection API — token, checksum, USSD push."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_TOKEN_CACHE: dict[str, Any] = {"token": None, "expires_at": 0.0}


def canonicalize(obj: Any) -> Any:
    if obj is None or not isinstance(obj, (dict, list)):
        return obj
    if isinstance(obj, list):
        return [canonicalize(item) for item in obj]
    return {key: canonicalize(obj[key]) for key in sorted(obj.keys())}


def create_payload_checksum(checksum_key: str, payload: dict[str, Any]) -> str:
    """HMAC-SHA256 hex digest per ClickPesa docs (exclude checksum fields from payload)."""
    clean = {k: v for k, v in payload.items() if k not in ("checksum", "checksumMethod")}
    canonical = canonicalize(clean)
    payload_string = json.dumps(canonical, separators=(",", ":"), ensure_ascii=False)
    return hmac.new(
        checksum_key.encode("utf-8"),
        payload_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_payload_checksum(checksum_key: str, payload: dict[str, Any], expected: str) -> bool:
    if not expected or not checksum_key:
        return False
    computed = create_payload_checksum(checksum_key, payload)
    return hmac.compare_digest(computed, expected.strip())


def clickpesa_configured() -> bool:
    return bool(settings.clickpesa_client_id and settings.clickpesa_api_key)


def make_order_reference() -> str:
    """Alphanumeric, max 20 chars (ClickPesa limit)."""
    suffix = secrets.token_hex(4).upper()
    return f"DP{suffix}"[:20]


def phone_for_clickpesa(raw: str) -> str | None:
    from app.core.tz_phone import normalize_tz_mobile_e164

    e164 = normalize_tz_mobile_e164(raw)
    if not e164:
        return None
    return e164.replace("+", "")


async def _get_bearer_token() -> str:
    if not clickpesa_configured():
        raise RuntimeError("ClickPesa is not configured on the server")

    now = time.time()
    cached = _TOKEN_CACHE.get("token")
    if cached and now < float(_TOKEN_CACHE.get("expires_at") or 0):
        return str(cached)

    url = f"{settings.clickpesa_api_base.rstrip('/')}/generate-token"
    async with httpx.AsyncClient(timeout=30.0) as client:
        res = await client.post(
            url,
            headers={
                "client-id": settings.clickpesa_client_id,
                "api-key": settings.clickpesa_api_key,
            },
        )
        res.raise_for_status()
        data = res.json()
        token = data.get("token")
        if not token:
            raise RuntimeError("ClickPesa token response missing token")
        token_str = str(token).strip()
        if not token_str.lower().startswith("bearer "):
            token_str = f"Bearer {token_str}"
        _TOKEN_CACHE["token"] = token_str
        _TOKEN_CACHE["expires_at"] = now + 55 * 60
        return token_str


def _with_checksum(payload: dict[str, Any]) -> dict[str, Any]:
    secret = settings.clickpesa_checksum_secret.strip()
    if not secret:
        return payload
    out = dict(payload)
    out["checksum"] = create_payload_checksum(secret, out)
    return out


async def preview_ussd_push(
    *,
    amount_tzs: int,
    order_reference: str,
    phone_number: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "amount": str(int(amount_tzs)),
        "currency": "TZS",
        "orderReference": order_reference[:20],
    }
    if phone_number:
        payload["phoneNumber"] = phone_number
    body = _with_checksum(payload)
    token = await _get_bearer_token()
    url = f"{settings.clickpesa_api_base.rstrip('/')}/payments/preview-ussd-push-request"
    async with httpx.AsyncClient(timeout=45.0) as client:
        res = await client.post(url, json=body, headers={"Authorization": token})
        if res.status_code >= 400:
            logger.warning("ClickPesa preview failed: %s %s", res.status_code, res.text[:500])
            res.raise_for_status()
        return res.json()


async def initiate_ussd_push(
    *,
    amount_tzs: int,
    order_reference: str,
    phone_number: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "amount": str(int(amount_tzs)),
        "currency": "TZS",
        "orderReference": order_reference[:20],
        "phoneNumber": phone_number,
    }
    body = _with_checksum(payload)
    token = await _get_bearer_token()
    url = f"{settings.clickpesa_api_base.rstrip('/')}/payments/initiate-ussd-push-request"
    async with httpx.AsyncClient(timeout=45.0) as client:
        res = await client.post(url, json=body, headers={"Authorization": token})
        if res.status_code >= 400:
            logger.warning("ClickPesa initiate failed: %s %s", res.status_code, res.text[:500])
            res.raise_for_status()
        return res.json()


async def query_payment_by_order_reference(order_reference: str) -> list[dict[str, Any]]:
    token = await _get_bearer_token()
    ref = order_reference[:20]
    url = f"{settings.clickpesa_api_base.rstrip('/')}/payments/{ref}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        res = await client.get(url, headers={"Authorization": token})
        if res.status_code == 404:
            return []
        res.raise_for_status()
        data = res.json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return [data]
        return []
