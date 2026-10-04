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


class VendorBill(Base):
    """Vendor bill (Odoo in_invoice) — posts to journal on confirm."""

    __tablename__ = "vendor_bills"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    branch_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(60), default="Draft")
    state: Mapped[str] = mapped_column(String(20), default="draft")  # draft | posted | cancelled
    payment_state: Mapped[str] = mapped_column(String(20), default="not_paid")
    vendor_name: Mapped[str] = mapped_column(String(255), default="")
    vendor_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    vendor_bill_ref: Mapped[str] = mapped_column(String(120), default="")
    bill_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    currency_code: Mapped[str] = mapped_column(String(8), default="TZS")
    amount_untaxed: Mapped[float] = mapped_column(Float, default=0)
    amount_tax: Mapped[float] = mapped_column(Float, default=0)
    amount_total: Mapped[float] = mapped_column(Float, default=0)
    amount_residual: Mapped[float] = mapped_column(Float, default=0)
    lines_json: Mapped[str] = mapped_column(Text, default="[]")
    journal_entry_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("journal_entries.id"), nullable=True)
    acc_move_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    purchase_order_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SaleQuotation(Base):
    """Customer quotation (Odoo sale order draft/sent) — no ledger until invoiced."""

    __tablename__ = "sale_quotations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    branch_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(60), default="New")
    state: Mapped[str] = mapped_column(String(20), default="draft")  # draft | sent | sale | cancel
    customer_name: Mapped[str] = mapped_column(String(255), default="")
    customer_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    validity_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    quotation_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    payment_terms: Mapped[str] = mapped_column(String(40), default="immediate")
    amount_untaxed: Mapped[float] = mapped_column(Float, default=0)
    amount_tax: Mapped[float] = mapped_column(Float, default=0)
    amount_total: Mapped[float] = mapped_column(Float, default=0)
    lines_json: Mapped[str] = mapped_column(Text, default="[]")
    terms: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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
    profile_json: Mapped[str] = mapped_column(Text, default="{}")
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


class AccJournal(Base):
    """Odoo account.journal — sales, purchases, bank, cash, general."""

    __tablename__ = "acc_journals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    code: Mapped[str] = mapped_column(String(10))
    name: Mapped[str] = mapped_column(String(120))
    journal_type: Mapped[str] = mapped_column(String(20), default="general")
    default_account_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("ledger_accounts.id"), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class AccMove(Base):
    """Odoo account.move — immutable when posted."""

    __tablename__ = "acc_moves"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    branch_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    journal_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("acc_journals.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(64), default="/")
    move_type: Mapped[str] = mapped_column(String(20), default="entry")
    state: Mapped[str] = mapped_column(String(20), default="draft")
    payment_state: Mapped[str] = mapped_column(String(20), default="not_paid")
    date: Mapped[date] = mapped_column(Date)
    invoice_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    partner_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    partner_name: Mapped[str] = mapped_column(String(255), default="")
    ref: Mapped[str] = mapped_column(String(120), default="")
    narration: Mapped[str] = mapped_column(Text, default="")
    amount_untaxed: Mapped[float] = mapped_column(Float, default=0)
    amount_tax: Mapped[float] = mapped_column(Float, default=0)
    amount_total: Mapped[float] = mapped_column(Float, default=0)
    amount_residual: Mapped[float] = mapped_column(Float, default=0)
    source_type: Mapped[str] = mapped_column(String(40), default="")
    source_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reversed_move_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    posted_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AccMoveLine(Base):
    __tablename__ = "acc_move_lines"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    move_id: Mapped[str] = mapped_column(String(36), ForeignKey("acc_moves.id"), index=True)
    account_id: Mapped[str] = mapped_column(String(36), ForeignKey("ledger_accounts.id"), index=True)
    partner_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    partner_name: Mapped[str] = mapped_column(String(255), default="")
    name: Mapped[str] = mapped_column(String(255), default="")
    debit: Mapped[float] = mapped_column(Float, default=0)
    credit: Mapped[float] = mapped_column(Float, default=0)
    display_type: Mapped[str] = mapped_column(String(20), default="product")
    amount_residual: Mapped[float] = mapped_column(Float, default=0)
    reconciled: Mapped[bool] = mapped_column(Boolean, default=False)


class AccPayment(Base):
    __tablename__ = "acc_payments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    branch_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    payment_type: Mapped[str] = mapped_column(String(20), default="outbound")
    partner_type: Mapped[str] = mapped_column(String(20), default="supplier")
    partner_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    partner_name: Mapped[str] = mapped_column(String(255), default="")
    amount: Mapped[float] = mapped_column(Float, default=0)
    date: Mapped[date] = mapped_column(Date)
    journal_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("acc_journals.id"), nullable=True)
    move_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("acc_moves.id"), nullable=True)
    bill_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("vendor_bills.id"), nullable=True)
    reference: Mapped[str] = mapped_column(String(120), default="")
    state: Mapped[str] = mapped_column(String(20), default="posted")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AccFiscalPosition(Base):
    """Odoo account.fiscal.position — tax/account mapping (TZ standard vs export)."""

    __tablename__ = "acc_fiscal_positions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    code: Mapped[str] = mapped_column(String(30), default="standard")
    vat_output_account: Mapped[str] = mapped_column(String(20), default="2100")
    vat_input_account: Mapped[str] = mapped_column(String(20), default="1310")
    default_sale_tax_rate: Mapped[float] = mapped_column(Float, default=18.0)
    auto_apply: Mapped[bool] = mapped_column(Boolean, default=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class AccBankStatement(Base):
    __tablename__ = "acc_bank_statements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    journal_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("acc_journals.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(64), default="Statement")
    date_from: Mapped[date] = mapped_column(Date)
    date_to: Mapped[date] = mapped_column(Date)
    balance_start: Mapped[float] = mapped_column(Float, default=0)
    balance_end: Mapped[float] = mapped_column(Float, default=0)
    state: Mapped[str] = mapped_column(String(20), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AccBankStatementLine(Base):
    __tablename__ = "acc_bank_statement_lines"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    statement_id: Mapped[str] = mapped_column(String(36), ForeignKey("acc_bank_statements.id"), index=True)
    date: Mapped[date] = mapped_column(Date)
    payment_ref: Mapped[str] = mapped_column(String(120), default="")
    partner_name: Mapped[str] = mapped_column(String(255), default="")
    amount: Mapped[float] = mapped_column(Float, default=0)
    is_reconciled: Mapped[bool] = mapped_column(Boolean, default=False)
    move_line_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("acc_move_lines.id"), nullable=True)


class AccBillPurchaseMatch(Base):
    """Links vendor bill ↔ purchase order (Odoo bill matching)."""

    __tablename__ = "acc_bill_purchase_matches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    bill_id: Mapped[str] = mapped_column(String(36), ForeignKey("vendor_bills.id"), index=True)
    purchase_order_id: Mapped[str] = mapped_column(String(36), index=True)
    matched_amount: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AccFinancialReportLine(Base):
    __tablename__ = "acc_financial_report_lines"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    report_code: Mapped[str] = mapped_column(String(40), index=True)
    parent_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("acc_financial_report_lines.id"), nullable=True)
    sequence: Mapped[int] = mapped_column(default=0)
    name: Mapped[str] = mapped_column(String(255))
    line_type: Mapped[str] = mapped_column(String(20), default="sum")
    account_codes: Mapped[str] = mapped_column(Text, default="")
    account_types: Mapped[str] = mapped_column(Text, default="")
    sign: Mapped[int] = mapped_column(default=1)
    style: Mapped[str] = mapped_column(String(20), default="normal")
