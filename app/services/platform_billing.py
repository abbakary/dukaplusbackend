"""Platform billing settings — trial length, Lipa till, WhatsApp support."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PlatformBillingSettings

DEFAULT_BILLING = {
    "id": "default",
    "trial_days": 14,
    "grace_days": 0,
    "lipa_number": "0650124656",
    "lipa_name": "DUKAPLUS",
    "whatsapp_number": "0650124656",
    "support_note_en": (
        "After payment, send your business name + M-Pesa reference on WhatsApp for activation."
    ),
    "support_note_sw": (
        "Baada ya malipo, tuma jina la biashara + kumbukumbu ya M-Pesa kwenye WhatsApp ili kuamilisha."
    ),
}


def billing_settings_dict(row: PlatformBillingSettings | None) -> dict:
    if row is None:
        return dict(DEFAULT_BILLING)
    return {
        "id": row.id,
        "trial_days": int(row.trial_days or 14),
        "grace_days": int(row.grace_days if row.grace_days is not None else 0),
        "lipa_number": (row.lipa_number or DEFAULT_BILLING["lipa_number"]).strip(),
        "lipa_name": (row.lipa_name or DEFAULT_BILLING["lipa_name"]).strip(),
        "whatsapp_number": (row.whatsapp_number or DEFAULT_BILLING["whatsapp_number"]).strip(),
        "support_note_en": row.support_note_en or DEFAULT_BILLING["support_note_en"],
        "support_note_sw": row.support_note_sw or DEFAULT_BILLING["support_note_sw"],
    }


async def ensure_billing_settings(db: AsyncSession) -> PlatformBillingSettings:
    result = await db.execute(
        select(PlatformBillingSettings).where(PlatformBillingSettings.id == "default")
    )
    row = result.scalar_one_or_none()
    if row:
        return row
    row = PlatformBillingSettings(**DEFAULT_BILLING)
    db.add(row)
    await db.flush()
    return row


async def get_billing_settings(db: AsyncSession) -> dict:
    row = await ensure_billing_settings(db)
    return billing_settings_dict(row)


async def update_billing_settings(db: AsyncSession, patch: dict) -> dict:
    row = await ensure_billing_settings(db)
    if "trial_days" in patch and patch["trial_days"] is not None:
        row.trial_days = max(1, min(90, int(patch["trial_days"])))
    if "grace_days" in patch and patch["grace_days"] is not None:
        row.grace_days = max(0, min(30, int(patch["grace_days"])))
    if "lipa_number" in patch and patch["lipa_number"] is not None:
        row.lipa_number = str(patch["lipa_number"]).strip()[:40]
    if "lipa_name" in patch and patch["lipa_name"] is not None:
        row.lipa_name = str(patch["lipa_name"]).strip()[:100]
    if "whatsapp_number" in patch and patch["whatsapp_number"] is not None:
        row.whatsapp_number = str(patch["whatsapp_number"]).strip()[:40]
    if "support_note_en" in patch and patch["support_note_en"] is not None:
        row.support_note_en = str(patch["support_note_en"]).strip()
    if "support_note_sw" in patch and patch["support_note_sw"] is not None:
        row.support_note_sw = str(patch["support_note_sw"]).strip()
    await db.flush()
    return billing_settings_dict(row)


async def get_trial_days(db: AsyncSession) -> int:
    cfg = await get_billing_settings(db)
    return int(cfg["trial_days"])


async def get_grace_days(db: AsyncSession) -> int:
    cfg = await get_billing_settings(db)
    return int(cfg["grace_days"])
