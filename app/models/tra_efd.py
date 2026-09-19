import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _new_id() -> str:
    return str(uuid.uuid4())


class TenantTraEfdConfig(Base):
    """Per-tenant TRA VEFD credentials and synced fiscal device profile."""

    __tablename__ = "tenant_tra_efd_configs"

    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), primary_key=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    api_base_url: Mapped[str] = mapped_column(
        String(500),
        default="https://webshop.co.tz/api/public/index.php/api/v1",
    )
    client_id: Mapped[str] = mapped_column(String(255), default="")
    client_secret_enc: Mapped[str] = mapped_column(Text, default="")
    company_city: Mapped[str] = mapped_column(String(120), default="")
    company_mobile: Mapped[str] = mapped_column(String(40), default="")
    default_id_type: Mapped[str] = mapped_column(String(2), default="6")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    connection_status: Mapped[str] = mapped_column(String(20), default="not_tested")
    last_connection: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    company_vrn: Mapped[str] = mapped_column(String(80), default="")
    company_tin: Mapped[str] = mapped_column(String(80), default="")
    company_serial: Mapped[str] = mapped_column(String(80), default="")
    company_vin: Mapped[str] = mapped_column(String(80), default="")
    tax_office: Mapped[str] = mapped_column(String(120), default="")
    token_user_name: Mapped[str] = mapped_column(String(120), default="")
    token_email: Mapped[str] = mapped_column(String(255), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class FiscalReceiptRecord(Base):
    """Persisted fiscal receipt submissions for audit, retry, and reporting."""

    __tablename__ = "fiscal_receipt_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    branch_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    sale_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    invoice_reference: Mapped[str] = mapped_column(String(120), default="")
    receipt_number: Mapped[str] = mapped_column(String(120), default="")
    verification_code: Mapped[str] = mapped_column(String(120), default="")
    verify_link: Mapped[str] = mapped_column(String(1000), default="")
    z_number: Mapped[str] = mapped_column(String(80), default="")
    vrn: Mapped[str] = mapped_column(String(80), default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    customer_name: Mapped[str] = mapped_column(String(255), default="")
    total_excl_tax: Mapped[float] = mapped_column(Float, default=0)
    total_tax: Mapped[float] = mapped_column(Float, default=0)
    total_incl_tax: Mapped[float] = mapped_column(Float, default=0)
    items_json: Mapped[list] = mapped_column(JSON, default=list)
    api_response_raw: Mapped[str] = mapped_column(Text, default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
