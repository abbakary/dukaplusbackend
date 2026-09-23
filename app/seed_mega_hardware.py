"""Mega hardware demo tenant — 50+ records per area, AI product images.

Login: hardware@sample.dukaplus.co.tz / demo123
Owner:  owner.jengo-mega-hardware@sample.dukaplus.co.tz
"""

from __future__ import annotations

import json
import logging
import random
import secrets
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from app.core.security import DEFAULT_PERMISSIONS, hash_password
from app.core.secret_box import encrypt_secret
from app.demo_media import ai_product_image_url, staff_avatar_url
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
    TenantSettings,
    TenantStatus,
    User,
    UserRole,
)
from app.models.accounting import HrPayrollContract, HrPayslip, JournalEntry, JournalLine
from app.models.tra_efd import FiscalReceiptRecord, TenantTraEfdConfig
from app.seed_sample_data import (
    CUSTOMER_NAMES,
    DEMO_PASSWORD,
    EXPENSE_TITLES,
    PRODUCT_CATALOG,
    _email,
    _phone,
)
from app.services.accounting_defaults import ensure_default_chart

logger = logging.getLogger(__name__)

MEGA_SLUG = "jengo-mega-hardware"
MEGA_OWNER_EMAIL = _email(f"owner.{MEGA_SLUG}")
MEGA_HARDWARE_ALIAS = _email("hardware")

MEGA_COUNTS = {
    "products": 50,
    "customers": 50,
    "sales": 50,
    "suppliers": 50,
    "expenses": 50,
    "calendar_events": 50,
    "purchase_orders": 50,
}

EXTRA_SUPPLIER_NAMES = [
    "Simba Cement Distributors", "Roofing Masters TZ", "Dar Plumbing Wholesale",
    "Kilimanjaro Paint House", "Coastal Timber Ltd", "Ubungo Electrical Depot",
    "Mwanza Steel Traders", "Arusha Builders Mart", "Zanzibar Hardware Imports",
    "Lake Zone Aggregates", "Metro Tools Tanzania", "Safari Fasteners Co",
    "Golden Gate Hardware", "Jengo Pro Supplies", "Bongo Building Centre",
    "Kinondoni Wholesale", "Temeke Trade Links", "Ilala Merchant Group",
    "Highland Cement Agency", "Urban Renovation Supply", "ProTile Distributors",
    "African Pipes Ltd", "Bright LED Tanzania", "Secure Lock Imports",
    "Heavy Duty Tools Co", "Sand & Gravel Express", "Finishing Touch Materials",
    "Contractor One Stop", "Wholesale Nails & Screws", "Green Valley Timber",
    "Power Cable Tanzania", "Tank & Pump Solutions", "Door World Ltd",
    "Paint Pro Tanzania", "Budget Builders Supply", "Elite Hardware Chain",
    "Mwenge Spares & Tools", "Kariakoo Trading Hub", "Mbezi Outlet Wholesale",
    "Morogoro Road Merchants", "Sam Nujoma Hardware", "Nyerere Road Supplies",
    "Buguruni Industrial Traders", "Tabata Hardware Express", "Goba Builders Depot",
    "Mikocheni Trade House", "Masaki Contractor Store", "Oysterbay Premium Tools",
    "Tegeta Logistics Hardware", "Kigamboni Port Imports",
]

HARDWARE_EXPENSE_TITLES = EXPENSE_TITLES + [
    ("Forklift Fuel — Yard", "operations", 95000),
    ("Cement Offload Labour", "operations", 65000),
    ("Showroom Display Refresh", "marketing", 120000),
    ("Warehouse Rent — Annex", "rent", 380000),
    ("Safety Boots Staff", "payroll", 240000),
]


async def repoint_hardware_alias(db) -> None:
    """Point hardware@sample.dukaplus.co.tz at the mega demo owner."""
    owner_result = await db.execute(select(User).where(User.email == MEGA_OWNER_EMAIL))
    owner = owner_result.scalar_one_or_none()
    if not owner:
        return
    alias_result = await db.execute(select(User).where(User.email == MEGA_HARDWARE_ALIAS))
    alias = alias_result.scalar_one_or_none()
    hashed = hash_password(DEMO_PASSWORD)
    if alias:
        alias.tenant_id = owner.tenant_id
        alias.staff_id = owner.staff_id
        alias.name = owner.name
        alias.phone = owner.phone
        alias.role = owner.role
        alias.hashed_password = hashed
    else:
        db.add(
            User(
                email=MEGA_HARDWARE_ALIAS,
                hashed_password=hashed,
                name=owner.name,
                phone=owner.phone,
                role=owner.role,
                tenant_id=owner.tenant_id,
                staff_id=owner.staff_id,
            )
        )


async def ensure_mega_hardware_demo() -> dict[str, int | bool]:
    """Idempotent: create Jengo Mega Hardware with 50+ rows per major area."""
    stats: dict[str, int | bool] = {"created": False}
    rng = random.Random(20260922)
    hashed = hash_password(DEMO_PASSWORD)

    async with AsyncSessionLocal() as db:
        existing = await db.execute(select(Tenant).where(Tenant.owner_email == MEGA_OWNER_EMAIL))
        tenant = existing.scalar_one_or_none()
        if tenant:
            await repoint_hardware_alias(db)
            await db.commit()
            stats["created"] = False
            stats["tenant_id"] = tenant.id
            return stats

        spec_name = "Jengo Mega Hardware & Builders"
        owner_name = "Rashid Hamisi"
        region, district = "Dar es Salaam", "Kinondoni"
        biz_type = BusinessType.hardware

        tenant = Tenant(
            name=spec_name,
            owner_name=owner_name,
            owner_email=MEGA_OWNER_EMAIL,
            owner_phone=_phone(710882001),
            business_type=biz_type,
            region=region,
            district=district,
            tin_number="TIN-155223908",
            license_number="BRELA-TZ-55412",
            plan=SaaSPlanTier.enterprise_chain,
            status=TenantStatus.active,
            tra_efd_serial="EFD-TZ-2026-JENGO",
            subscription_expiry=datetime.now(UTC) + timedelta(days=365),
        )
        db.add(tenant)
        await db.flush()

        branch_hq = Branch(
            tenant_id=tenant.id,
            name=f"{spec_name} — HQ Kinondoni",
            code="HQ01",
            branch_type="main_hq",
            status="active",
            region=region,
            district=district,
            address="Sam Nujoma Road, Kinondoni, Dar es Salaam",
            phone=_phone(220882001),
            tra_efd_serial=tenant.tra_efd_serial,
        )
        db.add(branch_hq)
        branch_mbezi = Branch(
            tenant_id=tenant.id,
            name=f"{spec_name} — Mbezi Outlet",
            code="MB02",
            branch_type="branch",
            status="active",
            region=region,
            district="Ubungo",
            address="Mbezi Beach Road, Dar es Salaam",
            phone=_phone(220882002),
            tra_efd_serial=tenant.tra_efd_serial,
        )
        db.add(branch_mbezi)
        await db.flush()

        owner_perms = dict(DEFAULT_PERMISSIONS["Owner"])
        owner_perms["avatar_url"] = staff_avatar_url(MEGA_OWNER_EMAIL)
        owner_staff = StaffMember(
            tenant_id=tenant.id,
            branch_id=branch_hq.id,
            name=owner_name,
            email=MEGA_OWNER_EMAIL,
            phone=_phone(710882001),
            role=StaffRole.owner,
            permissions=owner_perms,
        )
        db.add(owner_staff)
        await db.flush()

        db.add(
            User(
                email=MEGA_OWNER_EMAIL,
                hashed_password=hashed,
                name=owner_name,
                phone=_phone(710882001),
                role=UserRole.vendor_owner,
                tenant_id=tenant.id,
                staff_id=owner_staff.id,
            )
        )

        staff_specs = [
            (StaffRole.manager, "Amina Saidi", branch_hq.id),
            (StaffRole.accountant, "John Mrema", branch_hq.id),
            (StaffRole.storekeeper, "Omari Hamisi", branch_hq.id),
            (StaffRole.cashier, "Grace Mushi", branch_hq.id),
            (StaffRole.cashier, "Hassan Omar", branch_hq.id),
            (StaffRole.manager, "Lucy Temba", branch_mbezi.id),
            (StaffRole.storekeeper, "Peter Lyimo", branch_mbezi.id),
            (StaffRole.cashier, "Fatuma Hassan", branch_mbezi.id),
        ]
        staff_members: list[StaffMember] = [owner_staff]
        for sidx, (role, sname, bid) in enumerate(staff_specs):
            role_key = role.value
            staff_email = _email(f"{role_key.lower().replace(' ', '')}.{MEGA_SLUG}.{sidx}")
            perms = dict(DEFAULT_PERMISSIONS.get(role_key, DEFAULT_PERMISSIONS["Cashier"]))
            perms["avatar_url"] = staff_avatar_url(staff_email)
            sm = StaffMember(
                tenant_id=tenant.id,
                branch_id=bid,
                name=sname,
                email=staff_email,
                phone=_phone(310882000 + sidx),
                role=role,
                permissions=perms,
            )
            db.add(sm)
            await db.flush()
            staff_members.append(sm)
            db.add(
                User(
                    email=staff_email,
                    hashed_password=hashed,
                    name=sname,
                    phone=sm.phone,
                    role=UserRole.vendor_staff,
                    tenant_id=tenant.id,
                    staff_id=sm.id,
                )
            )

        catalog = PRODUCT_CATALOG[biz_type]
        products: list[Product] = []
        for pidx in range(MEGA_COUNTS["products"]):
            base = catalog[pidx % len(catalog)]
            pname, category, price, cost, stock, unit = base
            if pidx >= len(catalog):
                batch = pidx // len(catalog) + 1
                pname = f"{pname} — Batch {batch}"
            sku = f"JNG-{pidx + 1:03d}"
            price_var = round(price * (0.9 + (pidx % 9) * 0.015), 0)
            stock_var = max(8, int(stock * (0.75 + (pidx % 6) * 0.08)))
            branch_id = branch_mbezi.id if pidx % 5 == 0 else branch_hq.id
            product = Product(
                tenant_id=tenant.id,
                branch_id=branch_id,
                name=pname,
                category=category,
                sku=sku,
                barcode=f"628882{pidx:05d}",
                price=price_var,
                cost=cost,
                stock=stock_var,
                reorder_point=max(8, int(stock_var * 0.25)),
                unit=unit,
                business_type=biz_type,
                metadata_json={
                    "image_url": ai_product_image_url(pname, sku, pidx),
                    "image_source": "ai_pollinations",
                    "supplier_name": EXTRA_SUPPLIER_NAMES[pidx % len(EXTRA_SUPPLIER_NAMES)],
                    "material": category,
                },
            )
            db.add(product)
            products.append(product)
        await db.flush()
        stats["products"] = len(products)

        customer_pool = (CUSTOMER_NAMES * 3)[:MEGA_COUNTS["customers"]]
        customers: list[Customer] = []
        for cidx in range(MEGA_COUNTS["customers"]):
            cname = customer_pool[cidx] if cidx < len(customer_pool) else f"Contractor {cidx + 1}"
            customer = Customer(
                tenant_id=tenant.id,
                name=cname,
                phone=_phone(480882000 + cidx),
                email=f"buyer{cidx}.{MEGA_SLUG}@mail.co.tz",
                address=f"{district}, {region}",
                credit_limit=rng.choice([200_000, 500_000, 1_000_000, 2_000_000]),
                balance=rng.choice([0, 0, 25_000, 85_000, 150_000, 320_000]),
                loyalty_tier=rng.choice(["Bronze", "Silver", "Gold", "Platinum"]),
                loyalty_points=rng.randint(0, 1200),
            )
            db.add(customer)
            customers.append(customer)
        await db.flush()
        stats["customers"] = len(customers)

        for sidx in range(MEGA_COUNTS["sales"]):
            cust = rng.choice(customers)
            prod = rng.choice(products)
            qty = rng.randint(1, 8)
            line_total = prod.price * qty
            subtotal = line_total
            vat = round(subtotal * 0.18, 2)
            total = subtotal + vat
            paid = total if sidx % 4 != 3 else rng.uniform(0, total * 0.5)
            status = "completed" if paid >= total - 1 else "pending_completion"
            sale_date = datetime.now(UTC) - timedelta(days=rng.randint(0, 90), hours=rng.randint(0, 12))
            branch_id = prod.branch_id or branch_hq.id
            cashier = rng.choice(staff_members).name
            db.add(
                Sale(
                    tenant_id=tenant.id,
                    branch_id=branch_id,
                    receipt_number=f"RCP-JNG-{sale_date.strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}",
                    customer_id=cust.id,
                    customer_name=cust.name,
                    items=[
                        {
                            "product_id": prod.id,
                            "product_name": prod.name,
                            "quantity": qty,
                            "unit_price": prod.price,
                            "total": line_total,
                        }
                    ],
                    subtotal=subtotal,
                    vat_amount=vat,
                    total=total,
                    paid_amount=paid,
                    balance_remaining=max(0, total - paid),
                    payments=[{"method": rng.choice(["cash", "mpesa", "bank"]), "amount": paid}] if paid else [],
                    sale_type="full" if paid >= total - 1 else "credit",
                    cashier_name=cashier,
                    tra_efd_signature=f"TRA-EFD-{secrets.token_hex(6).upper()}" if status == "completed" else None,
                    status=status,
                    created_at=sale_date,
                )
            )
        stats["sales"] = MEGA_COUNTS["sales"]

        suppliers: list[Supplier] = []
        for sup_idx in range(MEGA_COUNTS["suppliers"]):
            sname = EXTRA_SUPPLIER_NAMES[sup_idx % len(EXTRA_SUPPLIER_NAMES)]
            if sup_idx >= len(EXTRA_SUPPLIER_NAMES):
                sname = f"{sname} #{sup_idx + 1}"
            sup = Supplier(
                tenant_id=tenant.id,
                name=sname,
                contact_person=f"Sales Rep {sup_idx + 1}",
                phone=_phone(580882000 + sup_idx),
                email=f"vendor{sup_idx}.{MEGA_SLUG}@trade.co.tz",
                category=rng.choice(["Building", "Roofing", "Plumbing", "Electrical", "Tools"]),
                outstanding_payable=rng.choice([0, 120_000, 450_000, 890_000]),
                lead_time_days=rng.choice([2, 5, 7, 14, 21]),
                rating=round(rng.uniform(3.8, 5.0), 1),
            )
            db.add(sup)
            suppliers.append(sup)
        await db.flush()
        stats["suppliers"] = len(suppliers)

        for po_idx in range(MEGA_COUNTS["purchase_orders"]):
            sup = suppliers[po_idx % len(suppliers)]
            po_products = rng.sample(products, k=min(3, len(products)))
            items = []
            subtotal = 0.0
            for p in po_products:
                q = rng.randint(5, 40)
                subtotal += p.cost * q
                items.append(
                    {
                        "product_id": p.id,
                        "product_name": p.name,
                        "quantity": q,
                        "unit_cost": p.cost,
                        "total": p.cost * q,
                    }
                )
            db.add(
                PurchaseOrder(
                    tenant_id=tenant.id,
                    po_number=f"PO-JNG-{datetime.now(UTC).strftime('%Y%m')}-{po_idx + 1:03d}",
                    supplier_id=sup.id,
                    supplier_name=sup.name,
                    status=rng.choice(["draft", "sent", "received", "partial"]),
                    items=items,
                    subtotal=subtotal,
                    total_amount=subtotal,
                )
            )
        stats["purchase_orders"] = MEGA_COUNTS["purchase_orders"]

        expense_titles = HARDWARE_EXPENSE_TITLES
        for eidx in range(MEGA_COUNTS["expenses"]):
            title, category, amount = expense_titles[eidx % len(expense_titles)]
            db.add(
                Expense(
                    tenant_id=tenant.id,
                    title=title if eidx < len(expense_titles) else f"{title} #{eidx + 1}",
                    category=category,
                    amount=round(amount * rng.uniform(0.85, 1.15), 0),
                    payment_method=rng.choice(["cash_drawer", "mpesa", "bank"]),
                    recipient=owner_name if category == "payroll" else spec_name,
                    status=rng.choice(["paid", "paid", "pending"]),
                )
            )
        stats["expenses"] = MEGA_COUNTS["expenses"]

        for ev_idx in range(MEGA_COUNTS["calendar_events"]):
            ev_date = (datetime.now(UTC) + timedelta(days=ev_idx - 15)).strftime("%Y-%m-%d")
            db.add(
                CalendarEvent(
                    tenant_id=tenant.id,
                    title=rng.choice(
                        [
                            "Stock Count — Yard",
                            "Supplier Negotiation",
                            "Staff Safety Training",
                            "TRA VAT Filing",
                            "Promo — Cement Weekend",
                            "Forklift Maintenance",
                            "New Branch Delivery Run",
                        ]
                    ),
                    category=rng.choice(["inventory", "finance", "general", "operations"]),
                    event_date=ev_date,
                    event_time=rng.choice(["08:00", "10:30", "14:00", "16:00"]),
                    priority=rng.choice(["low", "medium", "high"]),
                    assigned_to=rng.choice(staff_members).name,
                )
            )
        stats["calendar_events"] = MEGA_COUNTS["calendar_events"]

        await _seed_compliance_extras(db, tenant, owner_staff, staff_members)

        await repoint_hardware_alias(db)
        await db.commit()
        stats["created"] = True
        stats["tenant_id"] = tenant.id
        logger.info("Created mega hardware demo tenant %s (%s)", tenant.name, tenant.id)
        return stats


async def _seed_compliance_extras(
    db,
    tenant: Tenant,
    owner_staff: StaffMember,
    staff_members: list[StaffMember],
) -> None:
    tenant_id = tenant.id
    tra = TenantTraEfdConfig(
        tenant_id=tenant_id,
        active=True,
        client_id="demo-jengo-client-id",
        client_secret_enc=encrypt_secret("demo-jengo-client-secret"),
        company_city="DAR ES SALAAM",
        company_mobile=tenant.owner_phone or "+255710882001",
        default_id_type="6",
        is_demo=True,
        connection_status="connected",
        last_connection=datetime.now(UTC),
        company_vrn="VRN-DEMO-JENGO-001",
        company_tin=tenant.tin_number or "TIN-155223908",
        company_serial=tenant.tra_efd_serial or "EFD-TZ-2026-JENGO",
        company_vin="VIN-JENGO-001",
        tax_office="Kinondoni Tax Office",
        token_user_name=tenant.owner_name,
        token_email=MEGA_OWNER_EMAIL,
    )
    db.add(tra)

    db.add(
        TenantSettings(
            tenant_id=tenant_id,
            business_settings={
                "mode": "tra_efd",
                "vatRegistered": True,
                "vatEnabled": True,
                "vatRate": 0.18,
                "pricesIncludeVat": True,
                "showVatOnReceipt": True,
                "showTraSignature": True,
                "traEfdSerial": tra.company_serial,
                "tinNumber": tenant.tin_number,
                "vrnNumber": tra.company_vrn,
                "receiptBusinessName": tenant.name,
                "receiptFooterNote": "Asante — Jengo Mega Hardware & Builders",
            },
            document_config={},
        )
    )

    accounts = await ensure_default_chart(db, tenant_id)
    by_code = {a.code: a for a in accounts}
    entry = JournalEntry(
        tenant_id=tenant_id,
        entry_date=date.today() - timedelta(days=5),
        reference="SAMPLE-JENGO-001",
        memo="Sample hardware sales (demo)",
        source="pos_sale",
    )
    db.add(entry)
    await db.flush()
    amount = 892_000.0
    cash, sales_acct, vat_acct = by_code.get("1000"), by_code.get("4000"), by_code.get("2100")
    if cash and sales_acct and vat_acct:
        net = round(amount / 1.18, 2)
        vat_amt = round(amount - net, 2)
        db.add(JournalLine(entry_id=entry.id, account_id=cash.id, label="Cash", debit=amount, credit=0))
        db.add(JournalLine(entry_id=entry.id, account_id=sales_acct.id, label="Sales", debit=0, credit=net))
        db.add(JournalLine(entry_id=entry.id, account_id=vat_acct.id, label="VAT", debit=0, credit=vat_amt))

    period = date.today().strftime("%Y-%m")
    for sm in staff_members[:8]:
        wage = 950_000 if sm.role == StaffRole.owner else rng_wage(sm.role)
        profile = {
            "baseSalary": wage,
            "housingAllowanceMonthly": round(wage * 0.12),
            "transportAllowanceMonthly": 66_000 if sm.role == StaffRole.owner else round(wage * 0.1),
            "tin": "109-442-671" if sm.role == StaffRole.owner else "109-xxx-000",
            "nssfNumber": f"TZ-NSSF-{sm.id.replace('-', '')[:8]}",
            "bankName": "CRDB Bank",
            "bankBranch": "Kariakoo",
            "bankAccount": "0150 2217634 01" if sm.role == StaffRole.owner else "",
            "department": "Management" if sm.role == StaffRole.owner else "Operations",
            "jobTitle": sm.role.value,
            "contractType": "Permanent",
            "heslb": False,
        }
        db.add(
            HrPayrollContract(
                tenant_id=tenant_id,
                staff_id=sm.id,
                staff_name=sm.name,
                wage_monthly=wage,
                structure_code="standard",
                nssf_enabled=True,
                paye_enabled=True,
                active=True,
                profile_json=json.dumps(profile),
            )
        )
        gross = 950_000 if sm.role == StaffRole.owner else rng_wage(sm.role)
        db.add(
            HrPayslip(
                tenant_id=tenant_id,
                staff_id=sm.id,
                staff_name=sm.name,
                period=period,
                gross_pay=gross,
                deductions=round(gross * 0.16, 0),
                net_pay=round(gross * 0.84, 0),
                status="draft",
                payslip_number=f"PS-{period}-JNG-{sm.id[:8]}",
                lines_json=json.dumps(
                    [
                        {"code": "BASIC", "name": "Basic salary", "amount": gross},
                        {"code": "NSSF", "name": "NSSF", "amount": -round(gross * 0.1, 0)},
                        {"code": "PAYE", "name": "PAYE", "amount": -round(gross * 0.06, 0)},
                    ]
                ),
            )
        )

    db.add(
        FiscalReceiptRecord(
            tenant_id=tenant_id,
            invoice_reference="RCP-JENGO-SAMPLE-001",
            receipt_number="RCP-JENGO-SAMPLE-001",
            verification_code="DEMO-JENGO-VERIFY",
            verify_link="https://verify.tra.go.tz/?vrn=VRN-DEMO-JENGO-001&code=DEMO-JENGO-VERIFY",
            z_number=tra.company_serial,
            vrn=tra.company_vrn,
            status="demo",
            is_demo=True,
            customer_name="Walk-in Contractor",
            total_excl_tax=755_932.2,
            total_tax=136_067.8,
            total_incl_tax=892_000,
            items_json=[{"itemdesc": "Cement & Iron Sheets bundle", "itemqty": 1, "amount": 892_000}],
            api_response_raw='{"status":"demo"}',
        )
    )


def rng_wage(role: StaffRole) -> float:
    return {
        StaffRole.manager: 720_000,
        StaffRole.accountant: 680_000,
        StaffRole.storekeeper: 520_000,
        StaffRole.cashier: 450_000,
    }.get(role, 480_000)
