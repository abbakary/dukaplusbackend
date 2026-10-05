"""Portal demo — 3 business tenants, 50+ rows per module, AI product images.

Login domain: *@portal.demo.dukaplus.co.tz  password: demo123
Super admin: admin@dukaplus.co.tz (from env)

Reset: python scripts/reset_and_seed_portal_demo.py
"""

from __future__ import annotations

import json
import logging
import random
import secrets
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select

from app.core.security import DEFAULT_PERMISSIONS, hash_password
from app.database import AsyncSessionLocal
from app.demo_media import portal_ai_product_image, staff_avatar_url
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
from app.models.accounting import SaleQuotation, VendorBill
from app.seed_sample_data import (
    CUSTOMER_NAMES,
    DEMO_PASSWORD,
    EXPENSE_TITLES,
    PRODUCT_CATALOG,
    SUPPLIER_NAMES,
    _phone,
)
from app.services.accounting_defaults import ensure_default_chart
from app.models.accounting import JournalEntry, JournalLine

logger = logging.getLogger(__name__)

PORTAL_EMAIL_DOMAIN = "@portal.demo.dukaplus.co.tz"
PORTAL_MARKER = "portal.demo.dukaplus.co.tz"
RECORDS_PER_MODULE = 50

PORTAL_TENANTS: list[dict] = [
    {
        "login": "pharmacy",
        "slug": "kariakoo-pharmacy",
        "name": "Kariakoo Family Pharmacy",
        "owner": "Dr. Neema Mwangi",
        "type": BusinessType.pharmacy,
        "plan": SaaSPlanTier.biashara_pro,
        "region": ("Dar es Salaam", "Ilala"),
    },
    {
        "login": "hardware",
        "slug": "sinza-hardware",
        "name": "Sinza Hardware & Building",
        "owner": "John Mrema",
        "type": BusinessType.hardware,
        "plan": SaaSPlanTier.biashara_pro,
        "region": ("Dar es Salaam", "Kinondoni"),
    },
    {
        "login": "retail",
        "slug": "mbezi-retail",
        "name": "Mbezi Beach General Store",
        "owner": "Fatuma Hassan",
        "type": BusinessType.retail,
        "plan": SaaSPlanTier.starter,
        "region": ("Dar es Salaam", "Kinondoni"),
    },
]

STAFF_ROLES = [
    StaffRole.manager,
    StaffRole.cashier,
    StaffRole.accountant,
    StaffRole.storekeeper,
]


def _portal_email(local: str) -> str:
    return f"{local}{PORTAL_EMAIL_DOMAIN}"


async def portal_demo_present() -> bool:
    async with AsyncSessionLocal() as db:
        n = await db.scalar(
            select(func.count(Tenant.id)).where(Tenant.owner_email.like(f"%{PORTAL_MARKER}"))
        )
        return int(n or 0) >= len(PORTAL_TENANTS)


async def seed_portal_demo() -> dict[str, list[str]]:
    """Insert 3 rich demo tenants (idempotent if already complete)."""
    if await portal_demo_present():
        logger.info("Portal demo already seeded (%s tenants)", len(PORTAL_TENANTS))
        return {"owners": [_portal_email(t["login"]) for t in PORTAL_TENANTS]}

    hashed = hash_password(DEMO_PASSWORD)
    rng = random.Random(20261005)
    owners: list[str] = []

    async with AsyncSessionLocal() as db:
        for tidx, spec in enumerate(PORTAL_TENANTS):
            login = spec["login"]
            slug = spec["slug"]
            biz_type: BusinessType = spec["type"]
            region, district = spec["region"]
            owner_email = _portal_email(login)

            tenant = Tenant(
                name=spec["name"],
                owner_name=spec["owner"],
                owner_email=owner_email,
                owner_phone=_phone(710000000 + tidx * 1000),
                business_type=biz_type,
                region=region,
                district=district,
                tin_number=f"TIN-{120000000 + tidx}",
                license_number=f"LIC-TZ-{2025000 + tidx}",
                plan=spec["plan"],
                status=TenantStatus.active,
                tra_efd_serial=f"EFD-{secrets.token_hex(4).upper()}",
                subscription_expiry=datetime.now(UTC) + timedelta(days=365),
            )
            db.add(tenant)
            await db.flush()

            branch = Branch(
                tenant_id=tenant.id,
                name=f"{spec['name']} — HQ",
                code=f"HQ{tidx + 1:02d}",
                branch_type="main_hq",
                status="active",
                region=region,
                district=district,
                address=f"{district}, {region}",
                phone=_phone(720000000 + tidx * 1000),
                tra_efd_serial=tenant.tra_efd_serial,
            )
            db.add(branch)
            await db.flush()

            owner_perms = dict(DEFAULT_PERMISSIONS["Owner"])
            owner_perms["avatar_url"] = staff_avatar_url(owner_email)
            owner_staff = StaffMember(
                tenant_id=tenant.id,
                branch_id=branch.id,
                name=spec["owner"],
                email=owner_email,
                phone=tenant.owner_phone,
                role=StaffRole.owner,
                permissions=owner_perms,
            )
            db.add(owner_staff)
            await db.flush()
            db.add(
                User(
                    email=owner_email,
                    hashed_password=hashed,
                    name=spec["owner"],
                    phone=tenant.owner_phone,
                    role=UserRole.vendor_owner,
                    tenant_id=tenant.id,
                    staff_id=owner_staff.id,
                )
            )
            owners.append(owner_email)

            for sidx, role in enumerate(STAFF_ROLES):
                role_key = role.value
                staff_email = _portal_email(f"{role_key.lower()}.{slug}")
                perms = dict(DEFAULT_PERMISSIONS.get(role_key, DEFAULT_PERMISSIONS["Cashier"]))
                perms["avatar_url"] = staff_avatar_url(staff_email)
                sm = StaffMember(
                    tenant_id=tenant.id,
                    branch_id=branch.id,
                    name=f"{role_key} — {spec['name'].split()[0]}",
                    email=staff_email,
                    phone=_phone(730000000 + tidx * 100 + sidx),
                    role=role,
                    permissions=perms,
                )
                db.add(sm)
                await db.flush()
                db.add(
                    User(
                        email=staff_email,
                        hashed_password=hashed,
                        name=sm.name,
                        phone=sm.phone,
                        role=UserRole.vendor_staff,
                        tenant_id=tenant.id,
                        staff_id=sm.id,
                    )
                )

            catalog = PRODUCT_CATALOG.get(biz_type, PRODUCT_CATALOG[BusinessType.retail])
            products: list[Product] = []
            for pidx in range(RECORDS_PER_MODULE):
                base = catalog[pidx % len(catalog)]
                pname, category, price, cost, stock, unit = base
                if pidx >= len(catalog):
                    pname = f"{pname} — #{pidx + 1}"
                sku = f"{slug[:4].upper()}-{pidx + 1:04d}"
                img = portal_ai_product_image(biz_type, pname, sku, pidx)
                product = Product(
                    tenant_id=tenant.id,
                    branch_id=branch.id,
                    name=pname,
                    category=category,
                    sku=sku,
                    barcode=f"628{tidx:02d}{pidx:06d}",
                    price=round(price * (0.9 + (pidx % 5) * 0.02), 0),
                    cost=cost,
                    stock=max(5, stock * (0.8 + (pidx % 4) * 0.05)),
                    reorder_point=max(5, stock * 0.2),
                    unit=unit,
                    business_type=biz_type,
                    requires_prescription=biz_type == BusinessType.pharmacy and pidx % 7 == 1,
                    metadata_json={
                        "image_url": img,
                        "image_source": "ai_pollinations",
                        "supplier_name": SUPPLIER_NAMES[pidx % len(SUPPLIER_NAMES)],
                    },
                )
                db.add(product)
                products.append(product)
            await db.flush()

            customers: list[Customer] = []
            for cidx in range(RECORDS_PER_MODULE):
                cname = CUSTOMER_NAMES[(tidx * 3 + cidx) % len(CUSTOMER_NAMES)]
                if cidx >= len(CUSTOMER_NAMES):
                    cname = f"{cname} {cidx + 1}"
                customers.append(
                    Customer(
                        tenant_id=tenant.id,
                        branch_id=branch.id,
                        name=cname,
                        phone=_phone(740000000 + tidx * 1000 + cidx),
                        email=f"c{cidx}.{slug}@mail.co.tz",
                        address=f"{district}, {region}",
                        credit_limit=rng.choice([100_000, 250_000, 500_000]),
                        balance=rng.choice([0, 0, 15_000, 45_000, 90_000]),
                        loyalty_tier=rng.choice(["Bronze", "Silver", "Gold"]),
                        loyalty_points=rng.randint(0, 800),
                    )
                )
            db.add_all(customers)
            await db.flush()

            for sidx in range(RECORDS_PER_MODULE):
                cust = customers[sidx % len(customers)]
                prod = products[sidx % len(products)]
                qty = rng.randint(1, 4)
                line_total = prod.price * qty
                vat = round(line_total * 0.18, 2)
                total = line_total + vat
                paid = total if sidx % 4 != 3 else rng.uniform(0, total * 0.5)
                paid = round(min(paid, total), 2)
                status = "completed" if paid >= total - 0.01 else "pending_completion"
                sale_date = datetime.now(UTC) - timedelta(days=rng.randint(0, 90), hours=rng.randint(0, 10))
                db.add(
                    Sale(
                        tenant_id=tenant.id,
                        branch_id=branch.id,
                        receipt_number=f"RCP-{sale_date.strftime('%Y%m%d')}-{tidx:01d}{sidx:04d}",
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
                        subtotal=line_total,
                        vat_amount=vat,
                        total=total,
                        paid_amount=paid,
                        balance_remaining=max(0, round(total - paid, 2)),
                        payments=[{"method": rng.choice(["cash", "mpesa"]), "amount": paid}] if paid else [],
                        sale_type="full" if paid >= total - 0.01 else "credit",
                        cashier_name=spec["owner"],
                        tra_efd_signature=f"TRA-{secrets.token_hex(5).upper()}" if status == "completed" else None,
                        status=status,
                        created_at=sale_date,
                    )
                )

            suppliers: list[Supplier] = []
            for sup_idx in range(RECORDS_PER_MODULE):
                suppliers.append(
                    Supplier(
                        tenant_id=tenant.id,
                        name=f"{SUPPLIER_NAMES[sup_idx % len(SUPPLIER_NAMES)]} — {sup_idx + 1}",
                        contact_person=f"Contact {sup_idx + 1}",
                        phone=_phone(750000000 + tidx * 1000 + sup_idx),
                        email=f"sup{sup_idx}.{slug}@trade.co.tz",
                        category=products[sup_idx % len(products)].category,
                        outstanding_payable=rng.choice([0, 35_000, 120_000, 250_000]),
                        lead_time_days=rng.choice([3, 7, 14, 21]),
                        rating=round(rng.uniform(3.5, 5.0), 1),
                    )
                )
            db.add_all(suppliers)
            await db.flush()

            for poidx in range(RECORDS_PER_MODULE):
                sup = suppliers[poidx % len(suppliers)]
                prods = [products[poidx % len(products)], products[(poidx + 7) % len(products)]]
                items = [
                    {
                        "product_id": p.id,
                        "product_name": p.name,
                        "quantity": 5 + (poidx % 10),
                        "unit_cost": p.cost,
                        "total": p.cost * (5 + (poidx % 10)),
                    }
                    for p in prods
                ]
                sub = sum(i["total"] for i in items)
                st = rng.choice(["draft", "confirmed", "received", "received", "partial"])
                db.add(
                    PurchaseOrder(
                        tenant_id=tenant.id,
                        branch_id=branch.id,
                        po_number=f"PO-{slug[:4].upper()}-{poidx + 1:04d}",
                        supplier_id=sup.id,
                        supplier_name=sup.name,
                        status=st,
                        items=items,
                        subtotal=sub,
                        total_amount=sub,
                        paid_amount=0 if st == "draft" else sub * 0.5,
                        expected_date=datetime.now(UTC) + timedelta(days=poidx % 14),
                        received_date=datetime.now(UTC) - timedelta(days=poidx % 30) if st == "received" else None,
                    )
                )

            for eidx in range(RECORDS_PER_MODULE):
                title, category, amount = EXPENSE_TITLES[eidx % len(EXPENSE_TITLES)]
                db.add(
                    Expense(
                        tenant_id=tenant.id,
                        title=title if eidx < len(EXPENSE_TITLES) else f"{title} #{eidx + 1}",
                        category=category,
                        amount=round(amount * rng.uniform(0.85, 1.15), 0),
                        payment_method=rng.choice(["cash_drawer", "mpesa", "bank"]),
                        recipient=spec["owner"] if category == "payroll" else spec["name"],
                        status="paid",
                        expense_date=datetime.now(UTC) - timedelta(days=eidx % 60),
                    )
                )

            for ev_idx in range(RECORDS_PER_MODULE):
                ev_date = (date.today() + timedelta(days=ev_idx - 25)).isoformat()
                db.add(
                    CalendarEvent(
                        tenant_id=tenant.id,
                        title=rng.choice(
                            ["Stock count", "Supplier visit", "TRA filing", "Staff training", "Promo launch", "Bank run"]
                        )
                        + f" #{ev_idx + 1}",
                        category=rng.choice(["inventory", "finance", "general", "hr"]),
                        event_date=ev_date,
                        event_time=rng.choice(["08:30", "10:00", "14:00", "16:30"]),
                        priority=rng.choice(["low", "medium", "high"]),
                        assigned_to=spec["owner"],
                    )
                )

            accounts = {a.code: a for a in await ensure_default_chart(db, tenant.id)}

            opening = JournalEntry(
                tenant_id=tenant.id,
                branch_id=branch.id,
                entry_date=date.today() - timedelta(days=45),
                reference="OPEN-PORTAL",
                memo="Portal demo opening balances",
                source="seed",
            )
            db.add(opening)
            await db.flush()
            for code, debit, credit, label in [
                ("1000", 3_000_000, 0, "Cash float"),
                ("1300", 2_000_000, 0, "Inventory"),
                ("3000", 0, 5_000_000, "Owner equity"),
            ]:
                acct = accounts.get(code)
                if acct:
                    db.add(
                        JournalLine(
                            entry_id=opening.id,
                            account_id=acct.id,
                            label=label,
                            debit=debit,
                            credit=credit,
                        )
                    )

            for bidx in range(RECORDS_PER_MODULE):
                sup = suppliers[bidx % len(suppliers)]
                prod = products[bidx % len(products)]
                qty = 2 + (bidx % 5)
                amt = round(prod.cost * qty, 2)
                tax = round(amt * 0.18, 2) if bidx % 3 == 0 else 0
                total = amt + tax
                lines = [
                    {
                        "label": prod.name,
                        "product_id": prod.id,
                        "product_name": prod.name,
                        "quantity": qty,
                        "price_unit": prod.cost,
                        "tax_rate": 18 if tax else 0,
                        "amount": amt,
                    }
                ]
                bill_state = "posted" if bidx % 5 == 0 else "draft"
                db.add(
                    VendorBill(
                        tenant_id=tenant.id,
                        branch_id=branch.id,
                        name="Draft" if bill_state == "draft" else f"BILL/2026/10/{bidx + 1:04d}",
                        state=bill_state,
                        payment_state="not_paid" if bill_state == "posted" else "not_paid",
                        vendor_name=sup.name,
                        vendor_id=sup.id,
                        vendor_bill_ref=f"INV-{slug}-{bidx + 1:04d}",
                        bill_date=date.today() - timedelta(days=bidx % 45),
                        due_date=date.today() + timedelta(days=30 - bidx % 20),
                        amount_untaxed=amt,
                        amount_tax=tax,
                        amount_total=total,
                        amount_residual=total,
                        lines_json=json.dumps(lines),
                    )
                )

            for qidx in range(RECORDS_PER_MODULE):
                cust = customers[qidx % len(customers)]
                prod = products[qidx % len(products)]
                qty = 1 + (qidx % 8)
                sub = round(prod.price * qty, 2)
                tax = round(sub * 0.18, 2)
                q_state = rng.choice(["draft", "draft", "sent", "sale"])
                db.add(
                    SaleQuotation(
                        tenant_id=tenant.id,
                        branch_id=branch.id,
                        name=f"QT/2026/{qidx + 1 + tidx * 100:04d}",
                        state=q_state,
                        customer_name=cust.name,
                        customer_id=cust.id,
                        validity_date=date.today() + timedelta(days=14 + qidx % 30),
                        quotation_date=date.today() - timedelta(days=qidx % 20),
                        payment_terms="immediate" if qidx % 2 == 0 else "net_30",
                        amount_untaxed=sub,
                        amount_tax=tax,
                        amount_total=sub + tax,
                        lines_json=json.dumps(
                            [
                                {
                                    "product_name": prod.name,
                                    "product_id": prod.id,
                                    "quantity": qty,
                                    "price_unit": prod.price,
                                    "tax_rate": 18,
                                    "amount": sub,
                                }
                            ]
                        ),
                        terms="Prices valid until validity date. VAT 18% where applicable.",
                    )
                )

            logger.info("Seeded portal tenant: %s (%s)", spec["name"], owner_email)

        await db.commit()

    return {"owners": owners, "password": DEMO_PASSWORD}
