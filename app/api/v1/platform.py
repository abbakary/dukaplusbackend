"""Public platform endpoints (landing page, no auth required)."""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import PlatformShowcaseItem

router = APIRouter(prefix="/platform", tags=["platform"])


class ShowcaseItemOut(BaseModel):
    id: str
    title: str
    subtitle: str | None
    media_type: str
    media_url: str
    thumbnail_url: str | None
    link_url: str | None
    sort_order: int
    is_featured: bool

    model_config = {"from_attributes": True}


@router.get("/showcase", response_model=list[ShowcaseItemOut])
async def list_showcase(
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(
        select(PlatformShowcaseItem)
        .where(PlatformShowcaseItem.is_active.is_(True))
        .order_by(PlatformShowcaseItem.is_featured.desc(), PlatformShowcaseItem.sort_order.asc())
    )
    return list(result.scalars().all())
