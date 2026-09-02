from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field


# ── Auth ──────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    device_info: str | None = None


class RegisterRequest(BaseModel):
    business_name: str
    owner_name: str
    email: EmailStr
    phone: str
    password: str = Field(min_length=6)
    business_type: str = "retail"
    tin_number: str = ""
    license_number: str = ""
    region: str = "Dar es Salaam"
    district: str = ""
    plan_tier: str = "free_starter"


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in_days: int = 3


class RefreshRequest(BaseModel):
    refresh_token: str


class UserResponse(BaseModel):
    id: str
    name: str
    email: str
    phone: str
    role: str
    tenant_id: str | None = None
    business_name: str | None = None
    business_type: str | None = None
    plan: str | None = None
    subscription_expiry: str | None = None
    tin_number: str | None = None
    license_number: str | None = None
    staff_role: str | None = None
    staff_id: str | None = None
    branch: str | None = None
    permissions: dict[str, bool] = {}
    language: str = "sw"
    status: str = "approved"

    model_config = {"from_attributes": True}


# ── Products ──────────────────────────────────────────────────────────────────

class ProductCreate(BaseModel):
    name: str
    category: str = "General"
    sku: str
    barcode: str | None = None
    price: float = 0
    cost: float = 0
    stock: float = 0
    reorder_point: float = 10
    unit: str = "pcs"
    batch_number: str | None = None
    expiry_date: datetime | None = None
    requires_prescription: bool = False
    branch_id: str | None = None
    metadata_json: dict[str, Any] = {}


class ProductUpdate(BaseModel):
    name: str | None = None
    category: str | None = None
    price: float | None = None
    cost: float | None = None
    stock: float | None = None
    reorder_point: float | None = None
    unit: str | None = None
    batch_number: str | None = None
    expiry_date: datetime | None = None
    requires_prescription: bool | None = None
    is_active: bool | None = None
    metadata_json: dict[str, Any] | None = None


class ProductResponse(BaseModel):
    id: str
    name: str
    category: str
    sku: str
    barcode: str | None = None
    price: float
    cost: float
    stock: float
    reorder_point: float
    unit: str
    batch_number: str | None = None
    expiry_date: datetime | None = None
    requires_prescription: bool
    business_type: str
    metadata_json: dict[str, Any] = {}
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Sales ─────────────────────────────────────────────────────────────────────

class SaleItemCreate(BaseModel):
    product_id: str
    product_name: str
    quantity: float
    unit_price: float
    total: float
    discount_percent: float = 0
    original_unit_price: float | None = None


class PaymentCreate(BaseModel):
    method: str
    amount: float
    reference: str | None = None


class SaleCreate(BaseModel):
    items: list[SaleItemCreate]
    customer_id: str | None = None
    customer_name: str | None = None
    payments: list[PaymentCreate] = []
    sale_type: str = "full"
    branch_id: str | None = None
    client_id: str | None = None
    finalize: bool = True


class SaleFinalize(BaseModel):
    payments: list[PaymentCreate] | None = None
    customer_id: str | None = None
    customer_name: str | None = None


class SaleResponse(BaseModel):
    id: str
    receipt_number: str
    customer_id: str | None = None
    customer_name: str | None = None
    items: list[dict]
    subtotal: float
    vat_amount: float
    total: float
    paid_amount: float
    balance_remaining: float
    payments: list[dict]
    sale_type: str
    cashier_name: str
    tra_efd_signature: str | None = None
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Customers ─────────────────────────────────────────────────────────────────

class CustomerCreate(BaseModel):
    name: str
    phone: str = ""
    email: str = ""
    address: str = ""
    credit_limit: float = 0
    notes: str | None = None


class CustomerUpdate(BaseModel):
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    credit_limit: float | None = None
    balance: float | None = None
    notes: str | None = None


class CustomerResponse(BaseModel):
    id: str
    name: str
    phone: str
    email: str
    address: str
    credit_limit: float
    balance: float
    loyalty_tier: str
    loyalty_points: int
    dunning_stage: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Dashboard ─────────────────────────────────────────────────────────────────

class DashboardStats(BaseModel):
    today_revenue: float
    today_sales_count: int
    total_products: int
    low_stock_count: int
    expiring_soon_count: int
    total_customers: int
    outstanding_receivables: float
    outstanding_payables: float
    monthly_revenue: float
    top_products: list[dict[str, Any]] = []
    cached: bool = False


class PageMeta(BaseModel):
    total: int
    skip: int
    limit: int
    has_more: bool


class PaginatedProducts(BaseModel):
    items: list[ProductResponse]
    meta: PageMeta


class PaginatedSales(BaseModel):
    items: list[SaleResponse]
    meta: PageMeta


class PaginatedCustomers(BaseModel):
    items: list[CustomerResponse]
    meta: PageMeta


class AnalyticsSnapshot(BaseModel):
    range: str
    gross_sales: float
    cogs: float
    gross_margin: float
    total_opex: float
    net_profit: float
    mom_change: dict[str, Any]
    category_profits: list[dict[str, Any]]
    monthly_pl: list[dict[str, Any]]
    top_products: list[dict[str, Any]]
    cost_savings: list[dict[str, Any]]
    cached: bool = False


# ── Sync ──────────────────────────────────────────────────────────────────────

class SyncItem(BaseModel):
    entity_type: str
    entity_id: str
    action: str
    payload: dict[str, Any]
    client_timestamp: datetime | None = None


class SyncBatchRequest(BaseModel):
    items: list[SyncItem]


class SyncBatchResponse(BaseModel):
    processed: int
    failed: int
    errors: list[str] = []
    server_timestamp: datetime


# ── Stock ─────────────────────────────────────────────────────────────────────

class StockAdjustment(BaseModel):
    product_id: str
    quantity: float
    movement_type: str
    batch_number: str | None = None
    expiry_date: datetime | None = None
    notes: str | None = None
    client_id: str | None = None


class StockMovementResponse(BaseModel):
    id: str
    product_id: str
    product_name: str
    sku: str
    movement_type: str
    quantity: float
    previous_stock: float
    new_stock: float
    operator_name: str
    notes: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
