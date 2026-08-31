"""Seed default landing page showcase items."""

from sqlalchemy import func, select

from app.database import AsyncSessionLocal
from app.models import PlatformShowcaseItem

DEFAULT_SHOWCASE = [
    {
        "title": "Duka+ POS in 60 seconds",
        "subtitle": "Sell faster, track stock, manage credit — on phone and desktop.",
        "media_type": "video",
        "media_url": "https://www.youtube.com/embed/ScMzIvxBSi4",
        "thumbnail_url": "https://images.unsplash.com/photo-1556742049-0cfed4f6a45d?w=1200&auto=format&fit=crop",
        "sort_order": 0,
        "is_featured": True,
        "is_active": True,
        "created_by": "Platform Admin",
    },
    {
        "title": "TRA EFD & VAT Compliance",
        "subtitle": "Receipts, signatures, and tax reports built for Tanzania.",
        "media_type": "image",
        "media_url": "https://images.unsplash.com/photo-1454165804603-c3d57bc86b40?w=800&auto=format&fit=crop",
        "sort_order": 1,
        "is_featured": False,
        "is_active": True,
        "created_by": "Platform Admin",
    },
    {
        "title": "Multi-branch Inventory",
        "subtitle": "Track stock across branches, transfers, and low-stock alerts.",
        "media_type": "image",
        "media_url": "https://images.unsplash.com/photo-1586528116311-ad8dd3c8310d?w=800&auto=format&fit=crop",
        "sort_order": 2,
        "is_featured": False,
        "is_active": True,
        "created_by": "Platform Admin",
    },
]


async def seed_platform_showcase() -> None:
    async with AsyncSessionLocal() as db:
        count = await db.scalar(select(func.count(PlatformShowcaseItem.id))) or 0
        if count > 0:
            return
        for item in DEFAULT_SHOWCASE:
            db.add(PlatformShowcaseItem(**item))
        await db.commit()
