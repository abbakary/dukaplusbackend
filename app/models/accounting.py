import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _new_id() -> str:
    return str(uuid.uuid4())


class LedgerAccount(Base):
    __tablename__ = "ledger_accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    code: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(255))
    account_type: Mapped[str] = mapped_column(String(30))  # asset, liability, equity, income, expense
    parent_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class JournalEntry(Base):
    __tablename__ = "journal_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    branch_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    entry_date: Mapped[date] = mapped_column(Date)
    reference: Mapped[str] = mapped_column(String(120), default="")
    memo: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(40), default="manual")  # manual, pos_sale, expense, payroll
    source_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JournalLine(Base):
    __tablename__ = "journal_lines"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    entry_id: Mapped[str] = mapped_column(String(36), ForeignKey("journal_entries.id"), index=True)
    account_id: Mapped[str] = mapped_column(String(36), ForeignKey("ledger_accounts.id"), index=True)
    label: Mapped[str] = mapped_column(String(255), default="")
    debit: Mapped[float] = mapped_column(Float, default=0)
    credit: Mapped[float] = mapped_column(Float, default=0)


class HrPayrollContract(Base):
    __tablename__ = "hr_payroll_contracts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    staff_id: Mapped[str] = mapped_column(String(36), index=True)
    staff_name: Mapped[str] = mapped_column(String(255), default="")
    wage_monthly: Mapped[float] = mapped_column(Float, default=0)
    structure_code: Mapped[str] = mapped_column(String(40), default="standard")
    nssf_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    paye_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class HrPayslip(Base):
    __tablename__ = "hr_payslips"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    staff_id: Mapped[str] = mapped_column(String(36), index=True)
    staff_name: Mapped[str] = mapped_column(String(255), default="")
    period: Mapped[str] = mapped_column(String(7), index=True)  # YYYY-MM
    lines_json: Mapped[str] = mapped_column(Text, default="[]")
    gross_pay: Mapped[float] = mapped_column(Float, default=0)
    deductions: Mapped[float] = mapped_column(Float, default=0)
    net_pay: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    payslip_number: Mapped[str] = mapped_column(String(60), default="")
    payment_reference: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
