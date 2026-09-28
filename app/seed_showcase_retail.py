"""Full-feature showcase retail demo — AI product photos, refunds for analysis.

Login: showcase@sample.dukaplus.co.tz / demo123
Owner:  owner.duka-plus-showcase@sample.dukaplus.co.tz
"""

from __future__ import annotations

import logging
import random
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.core.security import DEFAULT_PERMISSIONS, hash_password
from app.demo_media import ai_retail_product_image_url, staff_avatar_url
from app.database import AsyncSessionLocal
from app.models import (
    Branch,
    BusinessType,
    Customer,
    Product,
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
from app.seed_sample_data import CUSTOMER_NAMES, DEMO_PASSWORD, _email, _phone

logger = logging.getLogger(__name__)

SHOWCASE_SLUG = "duka-plus-showcase"
SHOWCASE_OWNER_EMAIL = _email(f"owner.{SHOWCASE_SLUG}")
SHOWCASE_ALIAS = _email("showcase")

SHOWCASE_PRODUCTS: list[tuple[str, str, float, float, float, str]] = [
    ("Bar Soap Classic 800g", "Personal Care", 2800, 1600, 180, "pcs"),
    ("Toothpaste Colgate 100g", "Personal Care", 4500, 2800, 120, "pcs"),
    ("Shampoo Sachet Pack x12", "Personal Care", 6500, 4200, 90, "packs"),
    ("Cooking Oil Sunflower 1L", "Groceries", 7200, 5400, 85, "bottles"),
    ("Rice Super 5kg", "Groceries", 19500, 14800, 55, "bags"),
    ("Sugar 2kg", "Groceries", 5800, 4400, 70, "packs"),
    ("Tea Leaves Chai Mix 250g", "Groceries", 4800, 3200, 95, "packs"),
    ("Maize Flour 2kg", "Groceries", 4200, 3100, 80, "packs"),
    ("Fresh Milk 500ml", "Dairy", 2200, 1650, 110, "cartons"),
    ("Eggs Tray 30pcs", "Dairy", 11500, 9200, 40, "trays"),
    ("Mineral Water 1.5L", "Drinks", 1400, 850, 200, "bottles"),
    ("Soft Drink 500ml", "Drinks", 1800, 1100, 150, "bottles"),
    ("Matches Box x10", "Household", 2500, 1200, 220, "boxes"),
    ("Washing Powder 1kg", "Household", 8500, 6200, 65, "packs"),
    ("Bleach 750ml", "Household", 3200, 2100, 75, "bottles"),
    ("Spaghetti 500g", "Groceries", 2900, 1900, 100, "packs"),
    ("Tomato Paste 400g", "Groceries", 3500, 2400, 88, "jars"),
    ("Biscuits Family Pack", "Snacks", 5200, 3600, 72, "packs"),
    ("Groundnuts Roasted 500g", "Snacks", 6500, 4800, 60, "packs"),
    ("Battery AA x4", "General", 4500, 2800, 130, "packs"),
    ("Torch LED Rechargeable", "General", 18500, 12500, 35, "pcs"),
    ("Notebook A5 80pg", "Stationery", 2200, 1400, 140, "pcs"),
]

REFUND_REASONS = [
    "Wrong item — customer returned unopened pack",
    "Damaged on shelf — quality check refund",
    "Price mismatch at checkout — goodwill refund",
    "Partial return — 2 of 5 units defective",
    "TRA receipt correction — partial refund approved",
    "Customer changed mind within store policy",
]


async def repoint_showcase_alias(db) -> None:
    owner_result = await db.execute(select(User).where(User.email == SHOWCASE_OWNER_EMAIL))
    owner = owner_result.scalar_one_or_none()
    if not owner:
        return
    hashed = hash_password(DEMO_PASSWORD)
    alias_result = await db.execute(select(User).where(User.email == SHOWCASE_ALIAS))
    alias = alias_result.scalar_one_or_none()
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
                email=SHOWCASE_ALIAS,
                hashed_password=hashed,
                name=owner.name,
                phone=owner.phone,
                role=owner.role,
                tenant_id=owner.tenant_id,
                staff_id=owner.staff_id,
            )
        )


async def _ensure_refund_demo_sales(db, tenant_id: str, branch_id: str, products: list[Product]) -> int:
    """Backfill refund-rich sales when tenant exists but has no refund rows."""
    refund_count = await db.scalar(
        select(func.count(Sale.id)).where(
            Sale.tenant_id == tenant_id,
            Sale.refunded_total > 0,
        )
    )
    if refund_count and refund_count >= 3:
        return 0

    rng = random.Random(20260928)
    added = 0
    now = datetime.now(UTC)
    for idx in range(6):
        prod = products[idx % len(products)]
        qty = rng.randint(2, 5)
        line_total = round(prod.price * qty, 2)
        vat = round(line_total * 0.18, 2)
        total = round(line_total + vat, 2)
        sale_date = now - timedelta(days=rng.randint(1, 25), hours=rng.randint(8, 18))
        refund_qty = 1 if idx % 2 == 0 else min(2, qty)
        refund_line = round(prod.price * refund_qty * 1.18, 2)
        is_full = idx == 5
        refund_amount = total if is_full else refund_line
        status = "refunded" if is_full else "partially_refunded"
        reason = REFUND_REASONS[idx % len(REFUND_REASONS)]
        refund_id = str(uuid.uuid4())
        refund_dt = sale_date + timedelta(hours=rng.randint(1, 48))
        items = [
            {
                "product_id": prod.id,
                "product_name": prod.name,
                "quantity": qty,
                "unit_price": prod.price,
                "total": line_total,
                "refunded_quantity": qty if is_full else refund_qty,
            }
        ]
        refunds = [
            {
                "id": refund_id,
                "date": refund_dt.strftime("%Y-%m-%d %H:%M"),
                "reason": reason,
                "operator_name": "Grace Mushi",
                "amount": refund_amount,
                "kind": "full" if is_full else "items",
                "lines": [
                    {
                        "product_id": prod.id,
                        "product_name": prod.name,
                        "quantity": qty if is_full else refund_qty,
                        "line_amount": refund_amount,
                    }
                ],
            }
        ]
        db.add(
            Sale(
                tenant_id=tenant_id,
                branch_id=branch_id,
                receipt_number=f"RCP-SHW-{sale_date.strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}",
                customer_name=rng.choice(CUSTOMER_NAMES[:12]),
                items=items,
                subtotal=line_total,
                vat_amount=vat,
                total=total,
                paid_amount=total,
                balance_remaining=0,
                payments=[{"method": rng.choice(["cash", "mpesa"]), "amount": total}],
                sale_type="full",
                cashier_name="Grace Mushi",
                tra_efd_signature=f"TRA-EFD-SHW-{secrets.token_hex(5).upper()}",
                status=status,
                refunded_total=refund_amount,
                refunds=refunds,
                refund_reason=reason,
                refunded_by="Grace Mushi",
                refunded_at=refund_dt,
                created_at=sale_date,
            )
        )
        added += 1
    return added


async def ensure_showcase_retail_demo() -> dict[str, int | bool]:
    stats: dict[str, int | bool] = {"created": False}
    rng = random.Random(20260928)
    hashed = hash_password(DEMO_PASSWORD)

    async with AsyncSessionLocal() as db:
        existing = await db.execute(select(Tenant).where(Tenant.owner_email == SHOWCASE_OWNER_EMAIL))
        tenant = existing.scalar_one_or_none()
        if tenant:
            await repoint_showcase_alias(db)
            branch = (
                await db.execute(select(Branch).where(Branch.tenant_id == tenant.id).limit(1))
            ).scalar_one_or_none()
            products = (
                await db.execute(select(Product).where(Product.tenant_id == tenant.id))
            ).scalars().all()
            if branch and products:
                stats["refund_sales_added"] = await _ensure_refund_demo_sales(
                    db, tenant.id, branch.id, products,
                )
            await db.commit()
            stats["created"] = False
            stats["tenant_id"] = tenant.id
            return stats

        spec_name = "Duka+ Showcase Retail (Demo)"
        owner_name = "Neema Mwangi"
        region, district = "Dar es Salaam", "Ilala"
        biz_type = BusinessType.retail

        tenant = Tenant(
            name=spec_name,
            owner_name=owner_name,
            owner_email=SHOWCASE_OWNER_EMAIL,
            owner_phone=_phone(710883001),
            business_type=biz_type,
            region=region,
            district=district,
            tin_number="TIN-188223901",
            license_number="BRELA-TZ-88201",
            plan=SaaSPlanTier.biashara_pro,
            status=TenantStatus.active,
            tra_efd_serial="EFD-TZ-2026-SHOWCASE",
            subscription_expiry=datetime.now(UTC) + timedelta(days=365),
        )
        db.add(tenant)
        await db.flush()

        branch_hq = Branch(
            tenant_id=tenant.id,
            name=f"{spec_name} — Kariakoo",
            code="SH01",
            branch_type="main_hq",
            status="active",
            region=region,
            district=district,
            address="Kariakoo Market Street, Ilala, Dar es Salaam",
            phone=_phone(220883001),
            tra_efd_serial=tenant.tra_efd_serial,
        )
        db.add(branch_hq)
        await db.flush()

        owner_perms = dict(DEFAULT_PERMISSIONS["Owner"])
        owner_perms["avatar_url"] = staff_avatar_url(SHOWCASE_OWNER_EMAIL)
        owner_staff = StaffMember(
            tenant_id=tenant.id,
            branch_id=branch_hq.id,
            name=owner_name,
            email=SHOWCASE_OWNER_EMAIL,
            phone=_phone(710883001),
            role=StaffRole.owner,
            permissions=owner_perms,
        )
        db.add(owner_staff)
        await db.flush()

        db.add(
            User(
                email=SHOWCASE_OWNER_EMAIL,
                hashed_password=hashed,
                name=owner_name,
                phone=_phone(710883001),
                role=UserRole.vendor_owner,
                tenant_id=tenant.id,
                staff_id=owner_staff.id,
            )
        )

        cashier_email = _email(f"cashier.{SHOWCASE_SLUG}")
        cashier_perms = dict(DEFAULT_PERMISSIONS["Cashier"])
        cashier_perms["avatar_url"] = staff_avatar_url(cashier_email)
        cashier_staff = StaffMember(
            tenant_id=tenant.id,
            branch_id=branch_hq.id,
            name="Grace Mushi",
            email=cashier_email,
            phone=_phone(310883002),
            role=StaffRole.cashier,
            permissions=cashier_perms,
        )
        db.add(cashier_staff)
        await db.flush()

        products: list[Product] = []
        for pidx, row in enumerate(SHOWCASE_PRODUCTS):
            pname, category, price, cost, stock, unit = row
            sku = f"SHW-{pidx + 1:03d}"
            product = Product(
                tenant_id=tenant.id,
                branch_id=branch_hq.id,
                name=pname,
                category=category,
                sku=sku,
                barcode=f"628883{pidx:05d}",
                price=price,
                cost=cost,
                stock=int(stock),
                reorder_point=max(10, int(stock * 0.2)),
                unit=unit,
                business_type=biz_type,
                metadata_json={
                    "image_url": ai_retail_product_image_url(pname, sku, pidx),
                    "image_source": "ai_pollinations",
                    "showcase": True,
                },
            )
            db.add(product)
            products.append(product)
        await db.flush()
        stats["products"] = len(products)

        for cidx, cname in enumerate(CUSTOMER_NAMES[:15]):
            db.add(
                Customer(
                    tenant_id=tenant.id,
                    name=cname,
                    phone=_phone(480883000 + cidx),
                    email=f"buyer{cidx}.{SHOWCASE_SLUG}@mail.co.tz",
                    address=f"{district}, {region}",
                    credit_limit=rng.choice([100_000, 250_000, 500_000]),
                    balance=rng.choice([0, 0, 15_000, 45_000]),
                    loyalty_tier=rng.choice(["Bronze", "Silver", "Gold"]),
                    loyalty_points=rng.randint(0, 800),
                )
            )

        for sidx in range(3):
            db.add(
                Supplier(
                    tenant_id=tenant.id,
                    name=rng.choice(["Kariakoo Wholesalers", "Azam Distribution", "Bongo FMCG Ltd"]),
                    contact_person=f"Rep {sidx + 1}",
                    phone=_phone(580883000 + sidx),
                    email=f"supplier{sidx}.{SHOWCASE_SLUG}@trade.co.tz",
                    category=rng.choice(["Groceries", "Personal Care", "Household"]),
                )
            )

        await db.flush()

        now = datetime.now(UTC)
        for sidx in range(22):
            prod = products[sidx % len(products)]
            qty = rng.randint(1, 4)
            line_total = round(prod.price * qty, 2)
            vat = round(line_total * 0.18, 2)
            total = round(line_total + vat, 2)
            sale_date = now - timedelta(days=rng.randint(0, 45), hours=rng.randint(8, 19))
            db.add(
                Sale(
                    tenant_id=tenant.id,
                    branch_id=branch_hq.id,
                    receipt_number=f"RCP-SHW-{sale_date.strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}",
                    customer_name=rng.choice(CUSTOMER_NAMES[:12]),
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
                    paid_amount=total,
                    balance_remaining=0,
                    payments=[{"method": rng.choice(["cash", "mpesa", "airtel"]), "amount": total}],
                    sale_type="full",
                    cashier_name="Grace Mushi",
                    tra_efd_signature=f"TRA-EFD-SHW-{secrets.token_hex(5).upper()}",
                    status="completed",
                    created_at=sale_date,
                )
            )
        stats["sales"] = 22

        stats["refund_sales_added"] = await _ensure_refund_demo_sales(
            db, tenant.id, branch_hq.id, products,
        )

        await repoint_showcase_alias(db)
        await db.commit()
        stats["created"] = True
        stats["tenant_id"] = tenant.id
        logger.info("Showcase retail demo tenant ready: %s", SHOWCASE_ALIAS)
        return stats
