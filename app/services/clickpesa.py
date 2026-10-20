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

# Pre-KYC ClickPesa accounts: max TZS 100,000 per collection (see ClickPesa quickstart).
CLICKPESA_PRE_KYC_MAX_TZS = 100_000


class ClickPesaError(Exception):
    """ClickPesa HTTP/API failure with a safe message for clients."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        provider_body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.provider_body = provider_body


def _clickpesa_api_base() -> str:
    base = (settings.clickpesa_api_base or "").strip().rstrip("/")
    if not base:
        return "https://api.clickpesa.com/third-parties"
    if base.endswith("/third-parties"):
        return base
    if "clickpesa.com" in base and not base.endswith("/third-parties"):
        return f"{base}/third-parties"
    return base


def _parse_clickpesa_error(status_code: int, text: str) -> str:
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            for key in ("message", "error", "detail", "description"):
                val = data.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
    except json.JSONDecodeError:
        pass
    snippet = (text or "").strip()[:240]
    if snippet:
        return snippet
    return f"ClickPesa request failed (HTTP {status_code})"


def _invalidate_token_cache() -> None:
    _TOKEN_CACHE["token"] = None
    _TOKEN_CACHE["expires_at"] = 0.0


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


def clickpesa_checksum_configured() -> bool:
    return bool(settings.clickpesa_checksum_secret.strip())


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

    url = f"{_clickpesa_api_base()}/generate-token"
    async with httpx.AsyncClient(timeout=30.0) as client:
        res = await client.post(
            url,
            headers={
                "client-id": settings.clickpesa_client_id,
                "api-key": settings.clickpesa_api_key,
            },
        )
        if res.status_code >= 400:
            msg = _parse_clickpesa_error(res.status_code, res.text)
            logger.warning("ClickPesa token failed: %s %s", res.status_code, res.text[:500])
            raise ClickPesaError(
                msg,
                status_code=res.status_code,
                provider_body=res.text[:500],
            )
        data = res.json()
        token = data.get("token")
        if not token:
            raise ClickPesaError("ClickPesa token response missing token")
        token_str = str(token).strip()
        if not token_str.lower().startswith("bearer "):
            token_str = f"Bearer {token_str}"
        _TOKEN_CACHE["token"] = token_str
        _TOKEN_CACHE["expires_at"] = now + 55 * 60
        return token_str


def _with_checksum(payload: dict[str, Any]) -> dict[str, Any]:
    secret = settings.clickpesa_checksum_secret.strip()
    if not secret:
        logger.warning(
            "CLICKPESA_CHECKSUM_SECRET is empty; requests may fail if checksum is enabled in ClickPesa dashboard"
        )
        return payload
    out = dict(payload)
    out["checksum"] = create_payload_checksum(secret, out)
    return out


async def _post_json(path: str, body: dict[str, Any]) -> dict[str, Any]:
    token = await _get_bearer_token()
    url = f"{_clickpesa_api_base()}{path}"
    async with httpx.AsyncClient(timeout=45.0) as client:
        res = await client.post(
            url,
            json=body,
            headers={"Authorization": token, "Content-Type": "application/json"},
        )
        if res.status_code == 401:
            _invalidate_token_cache()
        if res.status_code >= 400:
            msg = _parse_clickpesa_error(res.status_code, res.text)
            logger.warning("ClickPesa POST %s failed: %s %s", path, res.status_code, res.text[:500])
            raise ClickPesaError(msg, status_code=res.status_code, provider_body=res.text[:500])
        data = res.json()
        if isinstance(data, dict):
            return data
        return {"data": data}


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
    return await _post_json("/payments/preview-ussd-push-request", body)


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
    return await _post_json("/payments/initiate-ussd-push-request", body)


async def query_payment_by_order_reference(order_reference: str) -> list[dict[str, Any]]:
    token = await _get_bearer_token()
    ref = order_reference[:20]
    url = f"{_clickpesa_api_base()}/payments/{ref}"
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
