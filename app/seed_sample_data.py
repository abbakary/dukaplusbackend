"""Rich sample dataset — 20+ tenants, users, products, customers, sales, etc.

All demo accounts use password: demo123

Short alias logins (one per business type):
  pharmacy@sample.dukaplus.co.tz, retail@sample.dukaplus.co.tz, etc.

Full owner emails: owner.{slug}@sample.dukaplus.co.tz
Staff emails:      {role}.{slug}@sample.dukaplus.co.tz
"""

from __future__ import annotations

import random
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.core.security import DEFAULT_PERMISSIONS, hash_password
from app.database import AsyncSessionLocal
from app.models import (
    Branch,
    BusinessType,
    CalendarEvent,
    Customer,
    Expense,
    Product,
    PurchaseOrder,
    SaaSPlanTier,
    Sale,
    StaffMember,
    StaffRole,
    Supplier,
    Tenant,
    TenantStatus,
    User,
    UserRole,
)

DEMO_PASSWORD = "demo123"
SAMPLE_EMAIL_DOMAIN = "@sample.dukaplus.co.tz"
SEED_MARKER = "sample.dukaplus.co.tz"

# Per-tenant demo volume (dashboard-rich for each staff role)
PRODUCTS_PER_TENANT = 30
CUSTOMERS_PER_TENANT = 30
SALES_PER_TENANT = 30
SUPPLIERS_PER_TENANT = 10
EXPENSES_PER_TENANT = 10
CALENDAR_EVENTS_PER_TENANT = 8

# Featured tenants (6 business types) get extra staff roles for role-based demos
FEATURED_SLUGS = {
    "kariakoo-pharmacy",
    "mlimani-mart",
    "mbezi-retail",
    "sinza-hardware",
    "slipway-restaurant",
    "samora-electronics",
}

REGIONS = [
    ("Dar es Salaam", "Ilala"),
    ("Dar es Salaam", "Kinondoni"),
    ("Dar es Salaam", "Temeke"),
    ("Arusha", "Arusha Urban"),
    ("Mwanza", "Nyamagana"),
    ("Dodoma", "Dodoma Urban"),
    ("Mbeya", "Mbeya City"),
    ("Morogoro", "Morogoro Urban"),
    ("Tanga", "Tanga City"),
    ("Zanzibar", "Mjini Magharibi"),
]

PLANS = [SaaSPlanTier.starter, SaaSPlanTier.biashara_pro, SaaSPlanTier.enterprise_chain]

STAFF_ROLES_FOR_SEED = [
    StaffRole.manager,
    StaffRole.cashier,
    StaffRole.pharmacist,
    StaffRole.storekeeper,
    StaffRole.accountant,
]

# 20 tenants — all 15 business types + 5 regional variants
TENANT_SPECS: list[dict] = [
    {"slug": "kariakoo-pharmacy", "name": "Kariakoo Family Pharmacy", "owner": "Dr. Neema Mwangi", "type": BusinessType.pharmacy, "plan": SaaSPlanTier.biashara_pro},
    {"slug": "mlimani-mart", "name": "Mlimani City Supermarket", "owner": "Rajesh Patel", "type": BusinessType.supermarket, "plan": SaaSPlanTier.enterprise_chain},
    {"slug": "mbezi-retail", "name": "Mbezi Beach General Store", "owner": "Fatuma Hassan", "type": BusinessType.retail, "plan": SaaSPlanTier.starter},
    {"slug": "sinza-hardware", "name": "Sinza Hardware & Building", "owner": "John Mrema", "type": BusinessType.hardware, "plan": SaaSPlanTier.biashara_pro},
    {"slug": "samora-electronics", "name": "Samora Electronics Hub", "owner": "Amina Saidi", "type": BusinessType.electronics, "plan": SaaSPlanTier.biashara_pro},
    {"slug": "ubungo-autoparts", "name": "Ubungo Auto Spare Parts", "owner": "Charles Mkumbo", "type": BusinessType.auto_parts, "plan": SaaSPlanTier.biashara_pro},
    {"slug": "kijitonyama-fashion", "name": "Kijitonyama Fashion House", "owner": "Grace Mushi", "type": BusinessType.fashion, "plan": SaaSPlanTier.starter},
    {"slug": "moshi-agrovet", "name": "Kilimanjaro Agrovet Centre", "owner": "Peter Lyimo", "type": BusinessType.agrovet, "plan": SaaSPlanTier.biashara_pro},
    {"slug": "masaki-beauty", "name": "Masaki Beauty Lounge", "owner": "Zainab Omar", "type": BusinessType.beauty, "plan": SaaSPlanTier.starter},
    {"slug": "mikocheni-salon", "name": "Mikocheni Salon & Spa", "owner": "Lucy Temba", "type": BusinessType.salon, "plan": SaaSPlanTier.starter},
    {"slug": "slipway-restaurant", "name": "Slipway Restaurant & Cafe", "owner": "Michael Ngowi", "type": BusinessType.restaurant, "plan": SaaSPlanTier.biashara_pro},
    {"slug": "posta-stationery", "name": "Posta Bookshop & Stationery", "owner": "Sarah Kimaro", "type": BusinessType.stationery, "plan": SaaSPlanTier.starter},
    {"slug": "mwanza-furniture", "name": "Lake View Furniture", "owner": "David Mwangosi", "type": BusinessType.furniture, "plan": SaaSPlanTier.biashara_pro},
    {"slug": "arusha-services", "name": "Arusha Tech Services", "owner": "Emmanuel Sanga", "type": BusinessType.service, "plan": SaaSPlanTier.starter},
    {"slug": "tabata-mixed", "name": "Tabata Mixed Traders", "owner": "Halima Juma", "type": BusinessType.mixed, "plan": SaaSPlanTier.biashara_pro},
    {"slug": "dodoma-pharmacy", "name": "Dodoma Central Pharmacy", "owner": "Rehema Msuya", "type": BusinessType.pharmacy, "plan": SaaSPlanTier.starter},
    {"slug": "mbeya-retail", "name": "Mbeya Corner Shop", "owner": "Joseph Mwakasege", "type": BusinessType.retail, "plan": SaaSPlanTier.starter},
    {"slug": "tanga-hardware", "name": "Tanga Builders Supply", "owner": "Omari Hamisi", "type": BusinessType.hardware, "plan": SaaSPlanTier.biashara_pro},
    {"slug": "zanzibar-restaurant", "name": "Forodhani Seafood Grill", "owner": "Salma Ali", "type": BusinessType.restaurant, "plan": SaaSPlanTier.biashara_pro},
    {"slug": "morogoro-agrovet", "name": "Morogoro Farmers Agrovet", "owner": "Godfrey Mrosso", "type": BusinessType.agrovet, "plan": SaaSPlanTier.starter},
]

# Primary demo tenant per business type → short login email {type}@sample.dukaplus.co.tz
PRIMARY_ALIAS_BY_TYPE: dict[BusinessType, str] = {
    BusinessType.pharmacy: "kariakoo-pharmacy",
    BusinessType.supermarket: "mlimani-mart",
    BusinessType.retail: "mbezi-retail",
    BusinessType.hardware: "sinza-hardware",
    BusinessType.electronics: "samora-electronics",
    BusinessType.auto_parts: "ubungo-autoparts",
    BusinessType.fashion: "kijitonyama-fashion",
    BusinessType.agrovet: "moshi-agrovet",
    BusinessType.beauty: "masaki-beauty",
    BusinessType.salon: "mikocheni-salon",
    BusinessType.restaurant: "slipway-restaurant",
    BusinessType.stationery: "posta-stationery",
    BusinessType.furniture: "mwanza-furniture",
    BusinessType.service: "arusha-services",
    BusinessType.mixed: "tabata-mixed",
}

PRODUCT_CATALOG: dict[BusinessType, list[tuple[str, str, float, float, float, str]]] = {
    BusinessType.pharmacy: [
        ("Paracetamol 500mg x100", "Pain Relief", 8500, 4200, 120, "tablets"),
        ("Amoxicillin 250mg", "Antibiotics", 12000, 6500, 80, "capsules"),
        ("ORS Sachets", "Rehydration", 1500, 600, 200, "sachets"),
        ("Vitamin C 1000mg", "Vitamins", 6000, 2800, 90, "tablets"),
        ("Cetirizine 10mg", "Allergy", 4500, 2000, 60, "tablets"),
    ],
    BusinessType.supermarket: [
        ("Sunflower Oil 1L", "Cooking Oil", 6500, 4800, 150, "bottles"),
        ("Rice Super 5kg", "Food Basket", 18500, 14000, 80, "bags"),
        ("Fresh Milk 1L", "Dairy", 3200, 2400, 100, "cartons"),
        ("Washing Powder 2kg", "Household", 9800, 7200, 45, "packs"),
        ("Mineral Water 1.5L", "Drinks", 1200, 700, 300, "bottles"),
    ],
    BusinessType.retail: [
        ("Bar Soap Classic", "Personal Care", 2500, 1400, 200, "pcs"),
        ("Matches Box", "Household", 500, 200, 500, "boxes"),
        ("Sugar 1kg", "Groceries", 3200, 2500, 120, "kg"),
        ("Tea Leaves 250g", "Groceries", 4500, 3200, 90, "packs"),
        ("Cooking Salt 500g", "Groceries", 1200, 800, 150, "packs"),
    ],
    BusinessType.hardware: [
        ("Cement 50kg", "Building", 22000, 18500, 40, "bags"),
        ("Iron Sheet Gauge 28", "Roofing", 45000, 38000, 25, "sheets"),
        ("PVC Pipe 2 inch", "Plumbing", 8500, 6000, 60, "pcs"),
        ("Paint Emulsion 20L", "Paint", 85000, 62000, 15, "buckets"),
        ("Nails 3 inch 1kg", "Fasteners", 4500, 2800, 80, "kg"),
    ],
    BusinessType.electronics: [
        ("Samsung A15 128GB", "Mobile Phones", 450000, 380000, 12, "pcs"),
        ("Fast Charger 25W", "Chargers", 35000, 22000, 30, "pcs"),
        ("Bluetooth Earbuds", "Audio", 45000, 28000, 25, "pcs"),
        ("HDMI Cable 2m", "Accessories", 12000, 6500, 40, "pcs"),
        ("Power Bank 10000mAh", "Accessories", 55000, 35000, 20, "pcs"),
    ],
    BusinessType.auto_parts: [
        ("Brake Pad Toyota Corolla", "Brake System", 85000, 52000, 18, "sets"),
        ("Engine Oil 5W-30 4L", "Filters & Fluids", 65000, 42000, 30, "bottles"),
        ("Air Filter Universal", "Filters & Fluids", 25000, 14000, 35, "pcs"),
        ("Spark Plug NGK", "Engine Parts", 18000, 9000, 50, "pcs"),
        ("Shock Absorber Rear", "Suspension", 180000, 120000, 8, "pcs"),
    ],
    BusinessType.fashion: [
        ("Men Cotton Shirt", "Men", 35000, 18000, 40, "pcs"),
        ("Women Kitenge Dress", "Women", 55000, 28000, 25, "pcs"),
        ("Kids School Uniform", "Kids", 42000, 22000, 30, "pcs"),
        ("Leather Sandals", "Shoes", 48000, 25000, 20, "pairs"),
        ("Handbag Medium", "Accessories", 65000, 32000, 15, "pcs"),
    ],
    BusinessType.agrovet: [
        ("Maize Seed Hybrid 2kg", "Seeds", 18000, 12000, 50, "bags"),
        ("NPK Fertilizer 50kg", "Fertilizers", 85000, 65000, 30, "bags"),
        ("Cattle Dewormer 500ml", "Veterinary", 28000, 16000, 25, "bottles"),
        ("Poultry Feed 25kg", "Animal Feeds", 42000, 32000, 40, "bags"),
        ("Sprayer Knapsack 16L", "Equipment", 95000, 65000, 10, "pcs"),
    ],
    BusinessType.beauty: [
        ("Moisturizing Cream 200ml", "Skincare", 22000, 12000, 35, "jars"),
        ("Shampoo Anti-Dandruff", "Haircare", 15000, 8000, 45, "bottles"),
        ("Lipstick Matte Set", "Makeup", 18000, 9000, 30, "sets"),
        ("Body Spray 150ml", "Fragrance", 12000, 6500, 40, "bottles"),
        ("Face Cleanser", "Skincare", 14000, 7500, 35, "bottles"),
    ],
    BusinessType.salon: [
        ("Hair Relaxer Kit", "Hair", 25000, 14000, 20, "kits"),
        ("Manicure Service", "Nails", 15000, 5000, 999, "service"),
        ("Haircut Men", "Hair", 8000, 2000, 999, "service"),
        ("Braiding Medium", "Hair", 45000, 12000, 999, "service"),
        ("Nail Polish", "Nails", 8000, 3500, 25, "bottles"),
    ],
    BusinessType.restaurant: [
        ("Chicken Biryani", "Main Course", 12000, 5500, 999, "plates"),
        ("Beef Mishkaki", "Grill", 8000, 3500, 999, "plates"),
        ("Fresh Juice Mango", "Drinks", 4000, 1500, 999, "glasses"),
        ("Chapati Plain", "Sides", 1000, 300, 999, "pcs"),
        ("Pilau Special", "Main Course", 10000, 4500, 999, "plates"),
    ],
    BusinessType.stationery: [
        ("Exercise Book 80pg", "School Supplies", 1500, 800, 200, "pcs"),
        ("Ballpoint Pen Blue", "Writing", 500, 200, 500, "pcs"),
        ("A4 Paper Ream", "Office", 18000, 12000, 40, "reams"),
        ("Mathematical Set", "School Supplies", 8500, 4500, 35, "sets"),
        ("Stapler Heavy Duty", "Office", 12000, 6500, 20, "pcs"),
    ],
    BusinessType.furniture: [
        ("Office Desk 120cm", "Office Furniture", 280000, 180000, 8, "pcs"),
        ("Dining Table 6-Seater", "Living Room", 650000, 420000, 5, "sets"),
        ("Foam Mattress 6x4", "Mattresses", 320000, 210000, 10, "pcs"),
        ("Plastic Chair", "Living Room", 35000, 18000, 30, "pcs"),
        ("Wardrobe 3-Door", "Bedroom", 480000, 310000, 4, "pcs"),
    ],
    BusinessType.service: [
        ("Laptop Repair Diagnostic", "IT Services", 25000, 5000, 999, "service"),
        ("Phone Screen Replace", "Mobile Repair", 85000, 45000, 999, "service"),
        ("Data Recovery 500GB", "IT Services", 120000, 30000, 999, "service"),
        ("Network Setup Office", "IT Services", 350000, 120000, 999, "service"),
        ("Annual Maintenance", "Support", 180000, 60000, 999, "service"),
    ],
    BusinessType.mixed: [
        ("Groceries Bundle", "General", 15000, 10000, 50, "packs"),
        ("Mobile Credit 1000", "Telecom", 1000, 950, 999, "vouchers"),
        ("Printing A4 B&W", "Services", 200, 50, 999, "pages"),
        ("Second Hand Bicycle", "General", 180000, 120000, 3, "pcs"),
        ("Household Bucket 20L", "General", 8000, 4500, 25, "pcs"),
    ],
}

CUSTOMER_NAMES = [
    "Maria Joseph", "Ahmed Mussa", "Elizabeth Komba", "James Mwangi", "Asha Ramadhani",
    "Patrick Nyoni", "Rehema Said", "George Mkenda", "Christina Lyimo", "Hassan Omar",
    "Joyce Temu", "Frank Mrosso", "Neema Kassim", "Peter Massawe", "Salma Haji",
    "Daniel Msigwa", "Grace Mushi", "Omari Juma", "Lucy Kimaro", "Joseph Ngowi",
    "Fatuma Ali", "Charles Mrema", "Amina Hassan", "John Lyimo", "Halima Saidi",
]

SUPPLIER_NAMES = [
    "Tanzania Wholesalers Ltd", "East Africa Distributors", "Kariakoo Supplies Co",
    "Coastal Trading House", "Highland Merchants", "Lake Zone Traders",
    "Metro Import Export", "Safari Bulk Suppliers", "Urban Retail Chain",
    "Golden Gate Trading",
]

EXPENSE_TITLES = [
    ("Rent — Shop Premises", "rent", 450000),
    ("Staff Posho August", "payroll", 180000),
    ("Electricity TANESCO", "utilities", 85000),
    ("TRA EFD Paper Rolls", "compliance", 45000),
    ("Transport — Delivery", "transport", 35000),
    ("Marketing Flyers", "marketing", 25000),
    ("Cleaning Supplies", "operations", 18000),
    ("Internet Bundle", "utilities", 50000),
    ("Security Guard Fee", "operations", 120000),
    ("Bank Charges", "finance", 15000),
]


def _phone(n: int) -> str:
    return f"+2557{n:08d}"


def _email(local: str) -> str:
    return f"{local}{SAMPLE_EMAIL_DOMAIN}"


async def _already_seeded(db) -> bool:
    result = await db.execute(
        select(func.count(Tenant.id)).where(Tenant.owner_email.like(f"%{SEED_MARKER}"))
    )
    return (result.scalar() or 0) >= len(TENANT_SPECS)


async def seed_sample_data() -> None:
    """Insert 20 demo tenants with products, customers, sales, staff users, etc."""
    async with AsyncSessionLocal() as db:
        if await _already_seeded(db):
            return

        hashed = hash_password(DEMO_PASSWORD)
        rng = random.Random(42)
        customer_pool = CUSTOMER_NAMES.copy()
        rng.shuffle(customer_pool)

        for idx, spec in enumerate(TENANT_SPECS):
            region, district = REGIONS[idx % len(REGIONS)]
            slug = spec["slug"]
            biz_type: BusinessType = spec["type"]
            owner_name: str = spec["owner"]
            owner_email = _email(f"owner.{slug}")

            tenant = Tenant(
                name=spec["name"],
                owner_name=owner_name,
                owner_email=owner_email,
                owner_phone=_phone(10000000 + idx),
                business_type=biz_type,
                region=region,
                district=district,
                tin_number=f"TIN-{100000000 + idx}",
                license_number=f"LIC-TZ-{2024000 + idx}",
                plan=spec.get("plan", PLANS[idx % len(PLANS)]),
                status=TenantStatus.active if idx % 5 != 4 else TenantStatus.pending_kyc,
                tra_efd_serial=f"EFD-{secrets.token_hex(4).upper()}",
                subscription_expiry=datetime.now(UTC) + timedelta(days=30 + idx * 3),
            )
            db.add(tenant)
            await db.flush()

            branch = Branch(
                tenant_id=tenant.id,
                name=f"{spec['name']} — HQ",
                code=f"HQ{idx + 1:02d}",
                branch_type="main_hq",
                status="active",
                region=region,
                district=district,
                address=f"{district}, {region}",
                phone=_phone(20000000 + idx),
                tra_efd_serial=tenant.tra_efd_serial,
            )
            db.add(branch)
            await db.flush()

            owner_staff = StaffMember(
                tenant_id=tenant.id,
                branch_id=branch.id,
                name=owner_name,
                email=owner_email,
                phone=_phone(10000000 + idx),
                role=StaffRole.owner,
                permissions=DEFAULT_PERMISSIONS["Owner"],
            )
            db.add(owner_staff)
            await db.flush()

            owner_user = User(
                email=owner_email,
                hashed_password=hashed,
                name=owner_name,
                phone=_phone(10000000 + idx),
                role=UserRole.vendor_owner,
                tenant_id=tenant.id,
                staff_id=owner_staff.id,
            )
            db.add(owner_user)

            staff_roles_to_add = [StaffRole.manager, StaffRole.cashier]
            if biz_type == BusinessType.pharmacy:
                staff_roles_to_add.append(StaffRole.pharmacist)
            elif biz_type in (BusinessType.supermarket, BusinessType.retail):
                staff_roles_to_add.append(StaffRole.storekeeper)
            else:
                staff_roles_to_add.append(StaffRole.accountant)

            for sidx, staff_role in enumerate(staff_roles_to_add[:3]):
                role_key = staff_role.value
                staff_email = _email(f"{role_key.lower().replace(' ', '')}.{slug}")
                staff_member = StaffMember(
                    tenant_id=tenant.id,
                    branch_id=branch.id,
                    name=f"{role_key} — {spec['name'].split()[0]}",
                    email=staff_email,
                    phone=_phone(30000000 + idx * 10 + sidx),
                    role=staff_role,
                    permissions=DEFAULT_PERMISSIONS.get(role_key, DEFAULT_PERMISSIONS["Cashier"]),
                )
                db.add(staff_member)
                await db.flush()

                db.add(User(
                    email=staff_email,
                    hashed_password=hashed,
                    name=staff_member.name,
                    phone=staff_member.phone,
                    role=UserRole.vendor_staff,
                    tenant_id=tenant.id,
                    staff_id=staff_member.id,
                ))

            catalog = PRODUCT_CATALOG.get(biz_type, PRODUCT_CATALOG[BusinessType.retail])
            products: list[Product] = []
            for pidx in range(PRODUCTS_PER_TENANT):
                base = catalog[pidx % len(catalog)]
                pname, category, price, cost, stock, unit = base
                if pidx >= len(catalog):
                    batch = pidx // len(catalog) + 1
                    pname = f"{pname} — Var {batch}"
                sku = f"{slug[:6].upper()}-{pidx + 1:03d}"
                price_var = round(price * (0.92 + (pidx % 7) * 0.02), 0)
                stock_var = max(5, int(stock * (0.7 + (pidx % 5) * 0.1)))
                product = Product(
                    tenant_id=tenant.id,
                    branch_id=branch.id,
                    name=pname,
                    category=category,
                    sku=sku,
                    barcode=f"628{idx:04d}{pidx:05d}",
                    price=price_var,
                    cost=cost,
                    stock=stock_var,
                    reorder_point=max(5, stock_var * 0.2),
                    unit=unit,
                    business_type=biz_type,
                    requires_prescription=biz_type == BusinessType.pharmacy and pidx % len(catalog) == 1,
                )
                db.add(product)
                products.append(product)
            await db.flush()

            customers: list[Customer] = []
            for cidx in range(CUSTOMERS_PER_TENANT):
                cname = customer_pool[(idx * 4 + cidx) % len(customer_pool)]
                if cidx >= len(customer_pool):
                    cname = f"{cname} {cidx + 1}"
                balance = rng.choice([0, 0, 0, 15000, 35000, 85000, 120000])
                customer = Customer(
                    tenant_id=tenant.id,
                    name=cname,
                    phone=_phone(40000000 + idx * 100 + cidx),
                    email=f"customer{cidx}.{slug}@mail.co.tz",
                    address=f"{district}, {region}",
                    credit_limit=rng.choice([100000, 200000, 500000]),
                    balance=balance,
                    loyalty_tier=rng.choice(["Bronze", "Silver", "Gold"]),
                    loyalty_points=rng.randint(0, 500),
                )
                db.add(customer)
                customers.append(customer)
            await db.flush()

            for sidx in range(SALES_PER_TENANT):
                cust = rng.choice(customers)
                prod = rng.choice(products)
                qty = rng.randint(1, 3)
                line_total = prod.price * qty
                subtotal = line_total
                vat = round(subtotal * 0.18, 2)
                total = subtotal + vat
                paid = total if sidx < 2 else 0
                status = "completed" if paid >= total else "pending_completion"
                sale_date = datetime.now(UTC) - timedelta(days=rng.randint(0, 60), hours=rng.randint(0, 12))

                sale = Sale(
                    tenant_id=tenant.id,
                    branch_id=branch.id,
                    receipt_number=f"RCP-{sale_date.strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}",
                    customer_id=cust.id,
                    customer_name=cust.name,
                    items=[{
                        "product_id": prod.id,
                        "product_name": prod.name,
                        "quantity": qty,
                        "unit_price": prod.price,
                        "total": line_total,
                    }],
                    subtotal=subtotal,
                    vat_amount=vat,
                    total=total,
                    paid_amount=paid,
                    balance_remaining=max(0, total - paid),
                    payments=[{"method": rng.choice(["cash", "mpesa", "cash"]), "amount": paid}] if paid else [],
                    sale_type="full" if paid >= total else "credit",
                    cashier_name=owner_name,
                    tra_efd_signature=f"TRA-EFD-{secrets.token_hex(6).upper()}" if status == "completed" else None,
                    status=status,
                    created_at=sale_date,
                )
                db.add(sale)

            for sup_idx in range(SUPPLIERS_PER_TENANT):
                db.add(Supplier(
                    tenant_id=tenant.id,
                    name=SUPPLIER_NAMES[(idx + sup_idx) % len(SUPPLIER_NAMES)],
                    contact_person=f"Contact {sup_idx + 1}",
                    phone=_phone(50000000 + idx * 10 + sup_idx),
                    email=f"supplier{sup_idx}.{slug}@trade.co.tz",
                    category=products[0].category if products else "General",
                    outstanding_payable=rng.choice([0, 45000, 120000]),
                    lead_time_days=rng.choice([3, 7, 14]),
                    rating=round(rng.uniform(3.5, 5.0), 1),
                ))
            await db.flush()

            sup_result = await db.execute(
                select(Supplier).where(Supplier.tenant_id == tenant.id).limit(1)
            )
            first_supplier = sup_result.scalar_one_or_none()
            if first_supplier and products:
                po_total = sum(p.cost * 10 for p in products[:2])
                db.add(PurchaseOrder(
                    tenant_id=tenant.id,
                    po_number=f"PO-{datetime.now(UTC).strftime('%Y%m%d')}-{idx + 1:03d}",
                    supplier_id=first_supplier.id,
                    supplier_name=first_supplier.name,
                    status=rng.choice(["draft", "sent", "received"]),
                    items=[{
                        "product_id": p.id,
                        "product_name": p.name,
                        "quantity": 10,
                        "unit_cost": p.cost,
                        "total": p.cost * 10,
                    } for p in products[:2]],
                    subtotal=po_total,
                    total_amount=po_total,
                ))

            for eidx in range(EXPENSES_PER_TENANT):
                title, category, amount = EXPENSE_TITLES[eidx % len(EXPENSE_TITLES)]
                db.add(Expense(
                    tenant_id=tenant.id,
                    title=title if eidx < len(EXPENSE_TITLES) else f"{title} #{eidx + 1}",
                    category=category,
                    amount=amount * rng.uniform(0.8, 1.2),
                    payment_method=rng.choice(["cash_drawer", "mpesa", "bank"]),
                    recipient=owner_name if category == "payroll" else spec["name"],
                    status="paid",
                ))

            for ev_idx in range(CALENDAR_EVENTS_PER_TENANT):
                ev_date = (datetime.now(UTC) + timedelta(days=ev_idx * 7 + idx)).strftime("%Y-%m-%d")
                db.add(CalendarEvent(
                    tenant_id=tenant.id,
                    title=rng.choice([
                        "Stock Count", "Supplier Meeting", "Staff Training",
                        "TRA Filing", "Promo Launch",
                    ]),
                    category=rng.choice(["inventory", "finance", "general"]),
                    event_date=ev_date,
                    event_time=rng.choice(["09:00", "11:00", "14:00"]),
                    priority=rng.choice(["low", "medium", "high"]),
                    assigned_to=owner_name,
                ))

        await db.commit()


async def seed_login_aliases() -> None:
    """Add short emails like pharmacy@sample.dukaplus.co.tz (same access as owner)."""
    hashed = hash_password(DEMO_PASSWORD)
    async with AsyncSessionLocal() as db:
        for biz_type, slug in PRIMARY_ALIAS_BY_TYPE.items():
            alias_email = _email(biz_type.value)
            existing_alias = await db.execute(select(User).where(User.email == alias_email))
            if existing_alias.scalar_one_or_none():
                continue

            owner_email = _email(f"owner.{slug}")
            owner_result = await db.execute(select(User).where(User.email == owner_email))
            owner = owner_result.scalar_one_or_none()
            if not owner:
                continue

            db.add(User(
                email=alias_email,
                hashed_password=hashed,
                name=owner.name,
                phone=owner.phone,
                role=owner.role,
                tenant_id=owner.tenant_id,
                staff_id=owner.staff_id,
            ))

        demo_email = _email("demo")
        existing_demo = await db.execute(select(User).where(User.email == demo_email))
        if not existing_demo.scalar_one_or_none():
            retail_owner = await db.execute(
                select(User).where(User.email == _email("owner.mbezi-retail"))
            )
            owner = retail_owner.scalar_one_or_none()
            if owner:
                db.add(User(
                    email=demo_email,
                    hashed_password=hashed,
                    name=owner.name,
                    phone=owner.phone,
                    role=owner.role,
                    tenant_id=owner.tenant_id,
                    staff_id=owner.staff_id,
                ))

        await db.commit()
