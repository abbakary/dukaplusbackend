"""Validate POS discounts, price overrides, and partial/credit sales against tenant settings + RBAC."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_user_permissions
from app.models import Product, User
from app.schemas import SaleItemCreate

DEFAULT_BUSINESS_SETTINGS: dict[str, Any] = {
    "discountEnabled": True,
    "maxDiscountPercent": 15,
    "showDiscountOnReceipts": True,
    "showDiscountOnDocuments": True,
    "cartDiscountEnabled": False,
    "priceOverrideEnabled": False,
    "partialPaymentEnabled": True,
    "negotiationEnabled": True,
    "vatEnabled": True,
    "vatRate": 0.18,
}


def merge_business_settings(raw: dict[str, Any] | None) -> dict[str, Any]:
    return {**DEFAULT_BUSINESS_SETTINGS, **(raw or {})}


async def validate_sale_pricing(
    db: AsyncSession,
    *,
    tenant_id: str,
    user: User,
    items: list[SaleItemCreate],
    sale_type: str,
    business_settings: dict[str, Any] | None,
) -> None:
    bs = merge_business_settings(business_settings)
    perms = get_user_permissions(user)
    max_disc = float(bs.get("maxDiscountPercent", 15))
    can_approve = bool(perms.get("canApproveDiscounts"))
    can_override = bool(perms.get("canOverridePrices")) or can_approve
    can_credit = bool(perms.get("canGiveCredit"))

    normalized_type = (sale_type or "full").lower()
    if normalized_type in {"partial", "credit"}:
        if not bs.get("partialPaymentEnabled", True):
            raise HTTPException(status_code=400, detail="Partial and credit sales are disabled for this shop.")
        if not bs.get("negotiationEnabled", True):
            raise HTTPException(status_code=400, detail="Payment negotiation is disabled for this shop.")
        if not can_credit:
            raise HTTPException(status_code=403, detail="Missing permission: canGiveCredit")

    for item in items:
        result = await db.execute(
            select(Product).where(Product.id == item.product_id, Product.tenant_id == tenant_id)
        )
        product = result.scalar_one_or_none()
        catalog_price = float(product.price) if product else float(item.unit_price)

        original = float(item.original_unit_price) if item.original_unit_price is not None else catalog_price
        if abs(float(item.unit_price) - original) > 0.01:
            if not bs.get("priceOverrideEnabled", False):
                raise HTTPException(status_code=400, detail="Manual price overrides are disabled.")
            if not can_override:
                raise HTTPException(status_code=403, detail="Missing permission: canOverridePrices")

        disc_pct = float(item.discount_percent or 0)
        if disc_pct > 0:
            if not bs.get("discountEnabled", True):
                raise HTTPException(status_code=400, detail="Discounts are disabled for this shop.")
            if disc_pct > max_disc and not can_approve:
                raise HTTPException(
                    status_code=403,
                    detail=f"Discount {disc_pct}% exceeds allowed {max_disc}% without manager approval.",
                )

        if disc_pct <= 0 and original > 0:
            inferred = max(0.0, (1 - (float(item.total) / (original * float(item.quantity)))) * 100)
            if inferred > 0.5:
                if not bs.get("discountEnabled", True):
                    raise HTTPException(status_code=400, detail="Discounts are disabled for this shop.")
                if inferred > max_disc and not can_approve:
                    raise HTTPException(
                        status_code=403,
                        detail=f"Discount exceeds allowed {max_disc}% without manager approval.",
                    )
