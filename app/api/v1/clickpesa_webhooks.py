"""ClickPesa webhook callbacks — verify checksum, activate subscriptions."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from app.config import settings
from app.database import AsyncSessionLocal
from app.models import SubscriptionPayment, Tenant
from app.services.clickpesa import verify_payload_checksum
from app.services.subscription_checkout import (
    SUCCESS_STATUSES,
    apply_subscription_payment_success,
    get_payment_by_order_reference,
    mark_subscription_payment_failed,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


async def _process_payment_event(data: dict[str, Any], *, success: bool) -> None:
    order_ref = str(data.get("orderReference") or "").strip()
    if not order_ref:
        logger.warning("ClickPesa webhook missing orderReference")
        return

    async with AsyncSessionLocal() as db:
        payment = await get_payment_by_order_reference(db, order_ref)
        if not payment:
            logger.warning("ClickPesa webhook unknown orderReference=%s", order_ref)
            return

        tenant = await db.get(Tenant, payment.tenant_id)
        if not tenant:
            return

        channel = str(data.get("channel") or "ClickPesa")
        provider_status = str(data.get("status") or ("SUCCESS" if success else "FAILED"))
        payment.clickpesa_id = str(data.get("id") or payment.clickpesa_id or "")[:64] or payment.clickpesa_id
        payment.provider_channel = channel[:40]
        if data.get("paymentReference"):
            payment.reference = str(data["paymentReference"])[:100]

        if success or provider_status.upper() in SUCCESS_STATUSES:
            await apply_subscription_payment_success(
                db,
                payment,
                tenant=tenant,
                provider_reference=str(data.get("paymentReference") or ""),
                channel=channel,
                provider_status=provider_status,
            )
        else:
            await mark_subscription_payment_failed(
                db,
                payment,
                message=str(data.get("message") or ""),
                provider_status=provider_status,
            )
        await db.commit()


@router.post("/clickpesa")
async def clickpesa_webhook(request: Request):
    """
    Configure in ClickPesa dashboard:
    https://YOUR-API/api/v1/webhooks/clickpesa
    Events: PAYMENT RECEIVED, PAYMENT FAILED (and others — ignored safely).
    """
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON") from exc

    if not isinstance(body, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payload")

    event = str(body.get("event") or "").strip().upper()
    data = body.get("data")
    if not isinstance(data, dict):
        data = {}

    secret = settings.clickpesa_checksum_secret.strip()
    if secret:
        expected = str(body.get("checksum") or data.get("checksum") or "").strip()
        if not expected or not verify_payload_checksum(secret, data, expected):
            logger.warning("ClickPesa webhook checksum rejected for event=%s", event)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid checksum")

    if event == "PAYMENT RECEIVED":
        await _process_payment_event(data, success=True)
    elif event == "PAYMENT FAILED":
        await _process_payment_event(data, success=False)
    else:
        logger.info("ClickPesa webhook ignored event=%s", event)

    return {"received": True}
