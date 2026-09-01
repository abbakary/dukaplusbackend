"""Document templates catalog and tenant document configuration."""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.deps import get_current_user, require_tenant
from app.core.document_catalog import BUILT_IN_DOCUMENT_CATALOG, DEFAULT_ACTIVE_TEMPLATES, DOCUMENT_TYPE_LABELS
from app.models import User

router = APIRouter(prefix="/tenant/documents", tags=["documents"])


class CatalogTemplateOut(BaseModel):
    id: str
    document_type: str
    name: str
    name_sw: str
    layout: str
    popular: bool = False


class DocumentCatalogOut(BaseModel):
    templates: list[CatalogTemplateOut]
    default_active: dict[str, str]
    type_labels: dict[str, dict[str, str]]


@router.get("/catalog", response_model=DocumentCatalogOut)
async def document_catalog(
    user: Annotated[User, Depends(get_current_user)],
):
    """Return built-in template catalog for delivery notes, order notes, and invoices."""
    require_tenant(user)
    return DocumentCatalogOut(
        templates=[CatalogTemplateOut(**t) for t in BUILT_IN_DOCUMENT_CATALOG],
        default_active=DEFAULT_ACTIVE_TEMPLATES,
        type_labels=DOCUMENT_TYPE_LABELS,
    )


