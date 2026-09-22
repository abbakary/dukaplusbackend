"""Demo product and staff imagery — Unsplash + AI (Pollinations) for rich hardware demos."""

from __future__ import annotations

import hashlib
from urllib.parse import quote

from app.models import BusinessType

_UNSPLASH = "https://images.unsplash.com/{photo_id}?w=480&h=480&auto=format&fit=crop&q=80"

# Curated photos — retail / East Africa–friendly product & portrait shots
_PRODUCT_PHOTOS: dict[BusinessType, list[str]] = {
    BusinessType.pharmacy: [
        "photo-1584308666744-24d5c474f2ae",
        "photo-1471864190281-a93a3070b6de",
        "photo-1587854692152-cf660f5970a0",
        "photo-1550572017-edd951aaee2c",
        "photo-1628771065518-0d82f1938462",
    ],
    BusinessType.supermarket: [
        "photo-1542838132-92c53300491e",
        "photo-1604719312566-8912e9227c6a",
        "photo-1578916171728-46686eac8d58",
        "photo-1586201375761-83865001e31c",
        "photo-1558618666-fcd25c85cd64",
    ],
    BusinessType.retail: [
        "photo-1607082350899-7e105aa8868b",
        "photo-1523275335684-37898b6baf30",
        "photo-1505740420928-5e560c06d30e",
        "photo-1572635196237-14b3f281503f",
        "photo-1560343090-f0409e92791a",
    ],
    BusinessType.hardware: [
        "photo-1504148455328-c376907a0816",
        "photo-1581094794329-c8112a89af12",
        "photo-1504328345606-18bbc8c9d7d1",
        "photo-1585771724684-f38226589f48",
        "photo-1621905251189-08b45d6a269e",
    ],
    BusinessType.electronics: [
        "photo-1511707171634-5f897ff02aa9",
        "photo-1527864550417-7fd91fc51a46",
        "photo-1498049794561-7780e7231661",
        "photo-1587825140708-dfaf72ae4b04",
        "photo-1593642632823-8f785ba67e45",
    ],
    BusinessType.restaurant: [
        "photo-1504674900247-0877df9cc836",
        "photo-1546069901-ba9599a7e63c",
        "photo-1565299624946-b28f40a0ae38",
        "photo-1567620905732-2d1ec7ab7445",
        "photo-1512621776951-a57141f2eefd",
    ],
}

_DEFAULT_PRODUCT_PHOTOS = [
    "photo-1564466809058-bfaba4c45d04",
    "photo-1472851294608-062f824d29cc",
    "photo-1441986300917-64674bd600d8",
    "photo-1556740758-90de374c12ad",
    "photo-1555529669-2269763671c8",
]

_STAFF_PORTRAITS = [
    "photo-1507003211169-0a1dd7228f2d",
    "photo-1494790108377-be9c29b29330",
    "photo-1500648767791-00dcc994a43e",
    "photo-1438761681033-6461ffad8d80",
    "photo-1472099645785-5658abf4ff4e",
    "photo-1534528741775-53994a69daeb",
    "photo-1519345182560-3f2917c472ef",
    "photo-1544005313-94ddf0286df2",
    "photo-1580489944761-15a19d654956",
    "photo-1560250097-0b93528c311a",
]


def _pick(pool: list[str], key: str) -> str:
    idx = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) % len(pool)
    return _UNSPLASH.format(photo_id=pool[idx])


def ai_product_image_url(product_name: str, sku: str, variant: int = 0) -> str:
    """AI-style catalog photo via Pollinations (no API key). Stable per SKU."""
    label = (product_name or "hardware item").split("—")[0].split(" - ")[0].strip()[:120]
    prompt = (
        f"Professional e-commerce product photo of {label}, building materials hardware store, "
        "centered, clean white background, studio lighting, sharp detail, commercial catalog"
    )
    seed = int(hashlib.sha256(f"{sku}:{variant}".encode()).hexdigest()[:8], 16) % 999_999
    return f"https://image.pollinations.ai/prompt/{quote(prompt)}?width=512&height=512&seed={seed}&nologo=true"


def product_image_url(
    business_type: BusinessType,
    product_key: str,
    variant: int = 0,
    *,
    product_name: str | None = None,
    use_ai: bool = False,
) -> str:
    if use_ai and product_name:
        return ai_product_image_url(product_name, product_key, variant)
    pool = _PRODUCT_PHOTOS.get(business_type) or _DEFAULT_PRODUCT_PHOTOS
    key = f"{business_type.value}:{product_key}:{variant}"
    return _pick(pool, key)


def staff_avatar_url(staff_key: str) -> str:
    return _pick(_STAFF_PORTRAITS, f"staff:{staff_key}")
