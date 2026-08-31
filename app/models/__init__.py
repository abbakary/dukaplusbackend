import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def new_id() -> str:
    return str(uuid.uuid4())


class UserRole(str, enum.Enum):
    super_admin = "super_admin"
    vendor_owner = "vendor_owner"
    vendor_staff = "vendor_staff"


class BusinessType(str, enum.Enum):
    pharmacy = "pharmacy"
    supermarket = "supermarket"
    retail = "retail"
    hardware = "hardware"
    electronics = "electronics"
    auto_parts = "auto_parts"
    fashion = "fashion"
    agrovet = "agrovet"
    beauty = "beauty"
    salon = "salon"
    restaurant = "restaurant"
    stationery = "stationery"
    furniture = "furniture"
    service = "service"
    mixed = "mixed"


class StaffRole(str, enum.Enum):
    owner = "Owner"
    manager = "Manager"
    pharmacist = "Pharmacist"
    cashier = "Cashier"
    storekeeper = "Storekeeper"
    accountant = "Accountant"


class TenantStatus(str, enum.Enum):
    active = "active"
    suspended = "suspended"
    pending_kyc = "pending_kyc"
    grace_period = "grace_period"


class SaaSPlanTier(str, enum.Enum):
    free_starter = "free_starter"
    biashara_pro = "biashara_pro"
    enterprise_chain = "enterprise_chain"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255))
    phone: Mapped[str] = mapped_column(String(50), default="")
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.vendor_owner)
    tenant_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("tenants.id"), nullable=True)
    staff_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("staff_members.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    language: Mapped[str] = mapped_column(String(5), default="sw")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped["Tenant | None"] = relationship(back_populates="users")
    staff: Mapped["StaffMember | None"] = relationship(back_populates="user")
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    device_info: Mapped[str | None] = mapped_column(String(500), nullable=True)

    user: Mapped["User"] = relationship(back_populates="refresh_tokens")


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255))
    owner_name: Mapped[str] = mapped_column(String(255))
    owner_email: Mapped[str] = mapped_column(String(255))
    owner_phone: Mapped[str] = mapped_column(String(50))
    business_type: Mapped[BusinessType] = mapped_column(Enum(BusinessType), default=BusinessType.retail)
    region: Mapped[str] = mapped_column(String(100), default="Dar es Salaam")
    district: Mapped[str] = mapped_column(String(100), default="")
    tin_number: Mapped[str] = mapped_column(String(50), default="")
    license_number: Mapped[str] = mapped_column(String(100), default="")
    plan: Mapped[SaaSPlanTier] = mapped_column(Enum(SaaSPlanTier), default=SaaSPlanTier.free_starter)
    status: Mapped[TenantStatus] = mapped_column(Enum(TenantStatus), default=TenantStatus.pending_kyc)
    tra_efd_serial: Mapped[str] = mapped_column(String(100), default="")
    subscription_expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    logo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    users: Mapped[list["User"]] = relationship(back_populates="tenant")
    branches: Mapped[list["Branch"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    products: Mapped[list["Product"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    customers: Mapped[list["Customer"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    staff_members: Mapped[list["StaffMember"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")


class Branch(Base):
    __tablename__ = "branches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    code: Mapped[str] = mapped_column(String(20))
    branch_type: Mapped[str] = mapped_column(String(30), default="main_hq")
    status: Mapped[str] = mapped_column(String(20), default="active")
    region: Mapped[str] = mapped_column(String(100), default="")
    district: Mapped[str] = mapped_column(String(100), default="")
    address: Mapped[str] = mapped_column(Text, default="")
    phone: Mapped[str] = mapped_column(String(50), default="")
    tra_efd_serial: Mapped[str] = mapped_column(String(100), default="")
    opening_hours: Mapped[str] = mapped_column(String(100), default="08:00 - 20:00")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    tenant: Mapped["Tenant"] = relationship(back_populates="branches")


class StaffMember(Base):
    __tablename__ = "staff_members"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    branch_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("branches.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255))
    phone: Mapped[str] = mapped_column(String(50), default="")
    role: Mapped[StaffRole] = mapped_column(Enum(StaffRole), default=StaffRole.cashier)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    permissions: Mapped[dict] = mapped_column(JSON, default=dict)
    pin_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    joined_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    tenant: Mapped["Tenant"] = relationship(back_populates="staff_members")
    user: Mapped["User | None"] = relationship(back_populates="staff", uselist=False)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    branch_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("branches.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(100), default="General")
    sku: Mapped[str] = mapped_column(String(100), index=True)
    barcode: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    price: Mapped[float] = mapped_column(Float, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0)
    stock: Mapped[float] = mapped_column(Float, default=0)
    reorder_point: Mapped[float] = mapped_column(Float, default=10)
    unit: Mapped[str] = mapped_column(String(30), default="pcs")
    batch_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    expiry_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    requires_prescription: Mapped[bool] = mapped_column(Boolean, default=False)
    business_type: Mapped[BusinessType] = mapped_column(Enum(BusinessType), default=BusinessType.retail)
    # Business-type specific fields stored as JSON
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    tenant: Mapped["Tenant"] = relationship(back_populates="products")


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    phone: Mapped[str] = mapped_column(String(50), default="")
    email: Mapped[str] = mapped_column(String(255), default="")
    address: Mapped[str] = mapped_column(Text, default="")
    credit_limit: Mapped[float] = mapped_column(Float, default=0)
    balance: Mapped[float] = mapped_column(Float, default=0)
    loyalty_tier: Mapped[str] = mapped_column(String(20), default="Bronze")
    loyalty_points: Mapped[int] = mapped_column(Integer, default=0)
    dunning_stage: Mapped[str] = mapped_column(String(30), default="cleared")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    tenant: Mapped["Tenant"] = relationship(back_populates="customers")


class Sale(Base):
    __tablename__ = "sales"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    branch_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("branches.id"), nullable=True)
    receipt_number: Mapped[str] = mapped_column(String(50), index=True)
    customer_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("customers.id"), nullable=True)
    customer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    items: Mapped[list] = mapped_column(JSON, default=list)
    subtotal: Mapped[float] = mapped_column(Float, default=0)
    vat_amount: Mapped[float] = mapped_column(Float, default=0)
    total: Mapped[float] = mapped_column(Float, default=0)
    paid_amount: Mapped[float] = mapped_column(Float, default=0)
    balance_remaining: Mapped[float] = mapped_column(Float, default=0)
    payments: Mapped[list] = mapped_column(JSON, default=list)
    sale_type: Mapped[str] = mapped_column(String(20), default="full")
    cashier_name: Mapped[str] = mapped_column(String(255), default="")
    cashier_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    tra_efd_signature: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="completed")
    synced: Mapped[bool] = mapped_column(Boolean, default=True)
    client_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StockMovement(Base):
    __tablename__ = "stock_movements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    product_id: Mapped[str] = mapped_column(String(36), ForeignKey("products.id"), index=True)
    product_name: Mapped[str] = mapped_column(String(255))
    sku: Mapped[str] = mapped_column(String(100))
    movement_type: Mapped[str] = mapped_column(String(30))
    quantity: Mapped[float] = mapped_column(Float)
    previous_stock: Mapped[float] = mapped_column(Float)
    new_stock: Mapped[float] = mapped_column(Float)
    batch_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    expiry_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reference_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reference_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    operator_name: Mapped[str] = mapped_column(String(255), default="")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    synced: Mapped[bool] = mapped_column(Boolean, default=True)
    client_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    contact_person: Mapped[str] = mapped_column(String(255), default="")
    phone: Mapped[str] = mapped_column(String(50), default="")
    email: Mapped[str] = mapped_column(String(255), default="")
    category: Mapped[str] = mapped_column(String(100), default="")
    payment_terms: Mapped[str] = mapped_column(String(100), default="Net 30 Days")
    outstanding_payable: Mapped[float] = mapped_column(Float, default=0)
    lead_time_days: Mapped[int] = mapped_column(Integer, default=7)
    rating: Mapped[float] = mapped_column(Float, default=5.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    po_number: Mapped[str] = mapped_column(String(50), index=True)
    supplier_id: Mapped[str] = mapped_column(String(36), ForeignKey("suppliers.id"))
    supplier_name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(30), default="draft")
    items: Mapped[list] = mapped_column(JSON, default=list)
    subtotal: Mapped[float] = mapped_column(Float, default=0)
    total_amount: Mapped[float] = mapped_column(Float, default=0)
    paid_amount: Mapped[float] = mapped_column(Float, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Expense(Base):
    __tablename__ = "expenses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(50))
    amount: Mapped[float] = mapped_column(Float)
    payment_method: Mapped[str] = mapped_column(String(30), default="cash_drawer")
    recipient: Mapped[str] = mapped_column(String(255), default="")
    reference_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="paid")
    expense_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CalendarEvent(Base):
    __tablename__ = "calendar_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(30), default="general")
    event_date: Mapped[str] = mapped_column(String(10))
    event_time: Mapped[str] = mapped_column(String(20), default="09:00")
    priority: Mapped[str] = mapped_column(String(10), default="medium")
    description: Mapped[str] = mapped_column(Text, default="")
    assigned_to: Mapped[str] = mapped_column(String(255), default="")
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SyncQueue(Base):
    """Offline sync queue for mobile/web clients."""
    __tablename__ = "sync_queue"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[str] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(20))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TenantWorkplaceState(Base):
    """Per-tenant dynamic workplace UI state (tables, KOT, appointments)."""
    __tablename__ = "tenant_workplace_states"
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), primary_key=True)
    state_json: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PlatformShowcaseItem(Base):
    """Landing page showcase — demo video, ads, and provider posts (managed by super admin)."""
    __tablename__ = "platform_showcase"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(255))
    subtitle: Mapped[str | None] = mapped_column(String(500), nullable=True)
    media_type: Mapped[str] = mapped_column(String(20), default="image")  # video | image
    media_url: Mapped[str] = mapped_column(String(1000))
    thumbnail_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    link_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
