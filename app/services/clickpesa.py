"""ClickPesa Collection API — token, checksum, USSD push."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import socket
import time
from dataclasses import dataclass
from typing import Any, Callable, Coroutine, TypeVar
from urllib.parse import urlparse, urlunparse

import httpx
import requests
from requests.adapters import HTTPAdapter
from requests import exceptions as requests_exc
from urllib3.util.retry import Retry

from app.config import settings

logger = logging.getLogger(__name__)

_TOKEN_CACHE: dict[str, Any] = {"token": None, "expires_at": 0.0}
# After connect failures, avoid hammering ClickPesa (and long checkout waits).
_CLICKPESA_COOLDOWN_UNTIL: float = 0.0
_CLICKPESA_COOLDOWN_SECONDS = 45.0

# Pre-KYC ClickPesa accounts: max TZS 100,000 per collection (see ClickPesa quickstart).
CLICKPESA_PRE_KYC_MAX_TZS = 100_000
# USSD collections below this often fail provider fee validation (use quickstart examples ≥ 1000).
CLICKPESA_MIN_COLLECTION_TZS = 1_000

DEFAULT_MOBILE_CHANNELS: tuple[str, ...] = (
    "M-PESA",
    "TIGO-PESA",
    "AIRTEL-MONEY",
    "HALOPESA",
)


def normalize_payment_channel(name: str | None) -> str | None:
    if not name or not str(name).strip():
        return None
    raw = str(name).upper().replace("_", "-").replace(" ", "").strip()
    aliases = {
        "MPESA": "M-PESA",
        "M-PESA": "M-PESA",
        "TIGOPESA": "TIGO-PESA",
        "TIGO-PESA": "TIGO-PESA",
        "MIXX": "TIGO-PESA",
        "MIXXBYYAS": "TIGO-PESA",
        "AIRTELMONEY": "AIRTEL-MONEY",
        "AIRTEL-MONEY": "AIRTEL-MONEY",
        "HALOPESA": "HALOPESA",
        "HALOTEL": "HALOPESA",
    }
    compact = raw.replace("-", "")
    for key, canonical in aliases.items():
        if raw == key or compact == key.replace("-", ""):
            return canonical
    if raw in DEFAULT_MOBILE_CHANNELS:
        return raw
    return str(name).upper().strip()


def merge_active_methods(
    raw: list[Any] | None,
    detected_provider: str | None = None,
) -> list[dict[str, Any]]:
    """Ensure all TZ wallets show in UI; overlay ClickPesa availability."""
    merged: dict[str, dict[str, Any]] = {
        ch: {"name": ch, "status": "UNAVAILABLE", "fee": None} for ch in DEFAULT_MOBILE_CHANNELS
    }
    if raw:
        for item in raw:
            if not isinstance(item, dict):
                continue
            canonical = normalize_payment_channel(str(item.get("name") or ""))
            if not canonical or canonical not in merged:
                continue
            merged[canonical] = {
                "name": canonical,
                "status": str(item.get("status") or "UNAVAILABLE"),
                "fee": item.get("fee"),
            }

    detected = normalize_payment_channel(detected_provider)
    if detected and detected in merged:
        merged[detected]["status"] = "AVAILABLE"

    if not any(m.get("status") == "AVAILABLE" for m in merged.values()):
        logger.info(
            "ClickPesa returned no AVAILABLE wallets; enabling all for checkout UI (push routes by phone).",
        )
        for m in merged.values():
            m["status"] = "AVAILABLE"

    return [merged[ch] for ch in DEFAULT_MOBILE_CHANNELS]


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


def clickpesa_client_http_status(status_code: int | None) -> int:
    """Map ClickPesa HTTP codes to FastAPI responses (avoid 502 for validation errors)."""
    if status_code is None:
        return 503
    if status_code == 401:
        return 503
    if 400 <= status_code < 500:
        return 400
    return 502


def validate_payment_channel_choice(
    selected: str | None,
    active_methods: list[Any],
    detected_provider: str | None = None,
) -> str:
    channel = normalize_payment_channel(selected) or "M-PESA"
    detected = normalize_payment_channel(detected_provider)
    if detected and detected != channel:
        raise ClickPesaError(
            f"This number is registered as {detected}. Switch to {detected} or use a {channel} number.",
        )
    methods = merge_active_methods(
        active_methods if isinstance(active_methods, list) else None,
        detected,
    )
    available = {normalize_payment_channel(m["name"]) for m in methods if m.get("status") == "AVAILABLE"}
    if available and channel not in available:
        raise ClickPesaError(
            f"{channel} is not available for this number or amount. Pick another wallet or check your phone number.",
        )
    return channel


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


def _mark_clickpesa_unreachable() -> None:
    global _CLICKPESA_COOLDOWN_UNTIL
    _CLICKPESA_COOLDOWN_UNTIL = time.time() + _CLICKPESA_COOLDOWN_SECONDS


def clickpesa_in_cooldown() -> bool:
    return time.time() < _CLICKPESA_COOLDOWN_UNTIL


def railway_egress_hint() -> str:
    if not os.environ.get("RAILWAY_ENVIRONMENT", "").strip():
        return ""
    return (
        " Railway fix: backend Service → Settings → Networking → turn OFF Static Outbound IPs → redeploy. "
        "Static IPs often break third-party HTTPS (works on your PC, times out on Railway)."
    )


@dataclass
class _ClickPesaHttpResponse:
    status_code: int
    text: str

    def json(self) -> Any:
        return json.loads(self.text)


def _requests_proxies() -> dict[str, str] | None:
    proxy = (settings.clickpesa_https_proxy or "").strip()
    if not proxy:
        return None
    return {"http": proxy, "https": proxy}


def _sync_clickpesa_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    json_body: dict[str, Any] | None = None,
) -> _ClickPesaHttpResponse:
    """Same stack as ClickPesa Python docs (requests + Session)."""
    connect = float(settings.clickpesa_connect_timeout_seconds or 20.0)
    timeout = (connect, 45.0)
    session = requests.Session()
    session.trust_env = bool(settings.clickpesa_trust_env)
    no_retry = Retry(total=0, connect=0, read=0, redirect=0, status=0)
    session.mount("https://", HTTPAdapter(max_retries=no_retry))
    session.mount("http://", HTTPAdapter(max_retries=no_retry))
    proxies = _requests_proxies()
    if method.upper() == "POST":
        res = session.post(url, headers=headers, json=json_body, timeout=timeout, proxies=proxies)
    else:
        res = session.get(url, headers=headers, timeout=timeout, proxies=proxies)
    return _ClickPesaHttpResponse(status_code=res.status_code, text=res.text)


def _clickpesa_timeout() -> httpx.Timeout:
    connect = float(settings.clickpesa_connect_timeout_seconds or 45.0)
    return httpx.Timeout(connect=connect, read=60.0, write=connect, pool=connect)


def _clickpesa_client(**kwargs: Any) -> httpx.AsyncClient:
    proxy = (settings.clickpesa_https_proxy or "").strip() or None
    trust_env = bool(settings.clickpesa_trust_env)
    return httpx.AsyncClient(
        timeout=_clickpesa_timeout(),
        trust_env=trust_env,
        proxy=proxy,
        **kwargs,
    )


async def _resolve_ipv4(hostname: str) -> str | None:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(
            hostname,
            443,
            family=socket.AF_INET,
            type=socket.SOCK_STREAM,
        )
        if infos:
            return str(infos[0][4][0])
    except OSError as exc:
        logger.debug("IPv4 lookup failed for %s: %s", hostname, exc)
    return None


def _url_for_ipv4(url: str, ipv4: str) -> tuple[str, dict[str, str]]:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    port = parsed.port
    netloc = f"{ipv4}:{port}" if port else ipv4
    rewritten = urlunparse(parsed._replace(netloc=netloc))
    return rewritten, {"Host": host} if host else {}


async def probe_clickpesa_tcp(*, host: str = "api.clickpesa.com", port: int = 443, timeout: float = 12.0) -> dict[str, Any]:
    """TCP reachability check (no ClickPesa credentials)."""
    started = time.monotonic()
    try:
        _reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        writer.close()
        await writer.wait_closed()
        ms = int((time.monotonic() - started) * 1000)
        return {"ok": True, "host": host, "port": port, "connect_ms": ms}
    except Exception as exc:
        ms = int((time.monotonic() - started) * 1000)
        err = str(exc).strip() or type(exc).__name__
        return {"ok": False, "host": host, "port": port, "connect_ms": ms, "error": err}


async def _clickpesa_http_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
) -> _ClickPesaHttpResponse:
    req_headers = dict(headers or {})

    async def _via_requests(target_url: str, extra_headers: dict[str, str]) -> _ClickPesaHttpResponse:
        merged = {**req_headers, **extra_headers}

        def _call() -> _ClickPesaHttpResponse:
            return _sync_clickpesa_request(method, target_url, headers=merged, json_body=json_body)

        return await asyncio.to_thread(_call)

    # Always call https://api.clickpesa.com/... — do not rewrite to raw IP (breaks TLS SNI).
    last_exc: BaseException | None = None
    for attempt in range(2):
        try:
            return await _via_requests(url, {})
        except (requests_exc.Timeout, requests_exc.ConnectionError) as exc:
            last_exc = exc
            if attempt == 0:
                await asyncio.sleep(1.0)
                continue
            break

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("unreachable")


def _is_connect_failure(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.ConnectTimeout, httpx.NetworkError)):
        return True
    return isinstance(
        exc,
        (
            requests_exc.ConnectTimeout,
            requests_exc.ConnectionError,
            requests_exc.Timeout,
        ),
    )


def _raise_clickpesa_transport_error(exc: BaseException, *, url: str) -> None:
    logger.error(
        "ClickPesa HTTP transport error for %s: %s (%s)",
        url,
        exc or type(exc).__name__,
        type(exc).__name__,
    )
    hint = railway_egress_hint()
    if _is_connect_failure(exc):
        _mark_clickpesa_unreachable()
        raise ClickPesaError(
            "Timed out connecting to ClickPesa from this server. "
            "Check GET /api/health/clickpesa. "
            "CLICKPESA_API_BASE should be https://api.clickpesa.com/third-parties."
            f"{hint}",
        ) from exc
    if isinstance(exc, (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout)):
        raise ClickPesaError(f"ClickPesa API timed out. Please try again.{hint}") from exc
    raise ClickPesaError(f"ClickPesa request failed: {exc}{hint}") from exc


T = TypeVar("T")


async def _with_transport_retry(coro_fn: Callable[[], Coroutine[Any, Any, T]]) -> T:
    last: BaseException | None = None
    for attempt in range(2):
        try:
            return await coro_fn()
        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.NetworkError) as exc:
            last = exc
            if attempt == 0:
                await asyncio.sleep(1.5)
                continue
            raise last
    raise RuntimeError("unreachable")


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
    if clickpesa_in_cooldown():
        raise ClickPesaError(
            "ClickPesa is unreachable from this server (recent connection failure). "
            "Fix Railway networking (disable Static Outbound IPs) then wait ~45 seconds or redeploy."
            f"{railway_egress_hint()}",
        )

    now = time.time()
    cached = _TOKEN_CACHE.get("token")
    if cached and now < float(_TOKEN_CACHE.get("expires_at") or 0):
        return str(cached)

    url = f"{_clickpesa_api_base()}/generate-token"
    try:
        res = await _clickpesa_http_request(
            "POST",
            url,
            headers={
                "client-id": settings.clickpesa_client_id,
                "api-key": settings.clickpesa_api_key,
            },
        )
    except BaseException as exc:
        _raise_clickpesa_transport_error(exc, url=url)

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
    global _CLICKPESA_COOLDOWN_UNTIL
    _CLICKPESA_COOLDOWN_UNTIL = 0.0
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

    try:
        res = await _clickpesa_http_request(
            "POST",
            url,
            headers={"Authorization": token, "Content-Type": "application/json"},
            json_body=body,
        )
    except BaseException as exc:
        _raise_clickpesa_transport_error(exc, url=url)

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
    fetch_sender_details: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "amount": str(int(amount_tzs)),
        "currency": "TZS",
        "orderReference": order_reference[:20],
    }
    if phone_number:
        payload["phoneNumber"] = phone_number
    if fetch_sender_details:
        payload["fetchSenderDetails"] = True
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
    try:
        res = await _clickpesa_http_request(
            "GET",
            url,
            headers={"Authorization": token},
        )
    except BaseException as exc:
        _raise_clickpesa_transport_error(exc, url=url)
    if res.status_code == 404:
        return []
    if res.status_code >= 400:
        msg = _parse_clickpesa_error(res.status_code, res.text)
        raise ClickPesaError(msg, status_code=res.status_code, provider_body=res.text[:500])
    data = res.json()
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    return []
