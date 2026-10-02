"""Minimal demo: wipe business data, seed super-admin path + 1 pharmacy (100+ SKUs, expiry, Rx).

Run:  python scripts/reset_and_seed_minimal.py
Or set SEED_MINIMAL_DEMO=true (and optionally RESET_DB_ON_MINIMAL_SEED=true) in .env
"""

from __future__ import annotations

import logging
import random
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.security import DEFAULT_PERMISSIONS, hash_password
from app.database import AsyncSessionLocal, Base, engine
from app.demo_media import ai_pharmacy_product_image_url, staff_avatar_url
from app.models import (
    Branch,
    BusinessType,
    Customer,
    Prescription,
    PrescriptionStatus,
    Product,
    SaaSPlanTier,
    Sale,
    StaffMember,
    StaffRole,
    Tenant,
    TenantStatus,
    User,
    UserRole,
)
from app.seed_sample_data import DEMO_PASSWORD

logger = logging.getLogger(__name__)

PHARMACY_SLUG = "kariakoo-pharmacy"
OWNER_EMAIL = "pharmacy@sample.dukaplus.co.tz"
PHARMACIST_EMAIL = "pharmacist.kariakoo-pharmacy@sample.dukaplus.co.tz"
PRODUCT_COUNT = 105

# (name, category, price, cost, base_stock, unit, requires_rx)
PHARMACY_SKU_BASE: list[tuple[str, str, float, float, float, str, bool]] = [
    ("Paracetamol 500mg x100", "Pain Relief", 8500, 4200, 120, "tablets", False),
    ("Ibuprofen 400mg x50", "Pain Relief", 7200, 3600, 80, "tablets", False),
    ("Amoxicillin 250mg caps", "Antibiotics", 12000, 6500, 45, "capsules", True),
    ("Azithromycin 500mg", "Antibiotics", 18500, 9800, 30, "tablets", True),
    ("Ciprofloxacin 500mg", "Antibiotics", 15000, 8200, 25, "tablets", True),
    ("Metronidazole 400mg", "Antibiotics", 6500, 3200, 60, "tablets", True),
    ("ORS Sachets 20-pack", "Rehydration", 4500, 1800, 90, "sachets", False),
    ("Zinc Sulphate 20mg", "Supplements", 5500, 2400, 70, "tablets", False),
    ("Vitamin C 1000mg", "Vitamins", 6000, 2800, 85, "tablets", False),
    ("Multivitamin Daily", "Vitamins", 12000, 5500, 55, "tablets", False),
    ("Cetirizine 10mg", "Allergy", 4500, 2000, 65, "tablets", False),
    ("Loratadine 10mg", "Allergy", 5200, 2400, 50, "tablets", False),
    ("Salbutamol Inhaler", "Respiratory", 8500, 4200, 22, "pcs", True),
    ("Beclomethasone Inhaler", "Respiratory", 22000, 12000, 12, "pcs", True),
    ("Artemether-Lumefantrine", "Antimalarial", 3500, 1600, 100, "tablets", True),
    ("Quinine Sulphate 300mg", "Antimalarial", 4800, 2200, 40, "tablets", True),
    ("Albendazole 400mg", "Antiparasitic", 2500, 900, 75, "tablets", False),
    ("Mebendazole 500mg", "Antiparasitic", 2800, 1100, 60, "tablets", False),
    ("Omeprazole 20mg", "GI", 6500, 3100, 48, "capsules", False),
    ("Antacid Suspension 200ml", "GI", 4200, 1900, 35, "bottles", False),
    ("Hyaluronic Face Serum", "Cosmetics", 28000, 14000, 18, "bottles", False),
    ("SPF50 Sunscreen 100ml", "Cosmetics", 22000, 11000, 20, "tubes", False),
    ("Shea Butter Body Lotion", "Cosmetics", 15000, 7000, 28, "bottles", False),
    ("Antiseptic Liquid 500ml", "First Aid", 5500, 2400, 42, "bottles", False),
    ("Elastic Bandage 10cm", "First Aid", 3500, 1200, 55, "pcs", False),
    ("Digital Thermometer", "Devices", 18000, 9000, 15, "pcs", False),
    ("Blood Pressure Cuff", "Devices", 45000, 24000, 8, "pcs", False),
    ("Glucose Test Strips x50", "Diabetes", 32000, 18000, 14, "boxes", False),
    ("Metformin 500mg", "Diabetes", 4500, 2100, 38, "tablets", True),
    ("Insulin Pen Needles", "Diabetes", 12000, 6000, 20, "boxes", True),
]


async def wipe_all_tables() -> None:
    import app.models  # noqa: F401 — register metadata

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database wiped and tables recreated")


async def seed_minimal_pharmacy_demo(*, skip_wipe: bool = False) -> dict[str, str]:
    """Idempotent when skip_wipe=True: skips if pharmacy tenant already exists."""
    if not skip_wipe:
        await wipe_all_tables()

    hashed = hash_password(DEMO_PASSWORD)
    rng = random.Random(42)

    async with AsyncSessionLocal() as db:
        if skip_wipe:
            existing = await db.scalar(select(Tenant.id).where(Tenant.owner_email == OWNER_EMAIL))
            if existing:
                logger.info("Minimal pharmacy demo already present — skipping")
                return {"owner": OWNER_EMAIL, "pharmacist": PHARMACIST_EMAIL, "skipped": "true"}

        tenant = Tenant(
            name="Kariakoo Family Pharmacy",
            owner_name="Dr. Neema Mwangi",
            owner_email=OWNER_EMAIL,
            owner_phone="+255712345678",
            business_type=BusinessType.pharmacy,
            region="Dar es Salaam",
            district="Ilala",
            tin_number="155-882-441",
            license_number="TMDA-PH-2024-8821",
            plan=SaaSPlanTier.biashara_pro,
            status=TenantStatus.active,
            subscription_expiry=datetime.now(UTC) + timedelta(days=365),
        )
        db.add(tenant)
        await db.flush()

        branch = Branch(
            tenant_id=tenant.id,
            name="Kariakoo Family Pharmacy - HQ",
            code="HQ01",
            branch_type="main_hq",
            region="Dar es Salaam",
            district="Ilala",
            address="Kariakoo, Dar es Salaam",
            phone="+255712345678",
        )
        db.add(branch)
        await db.flush()

        owner_perms = dict(DEFAULT_PERMISSIONS["Owner"])
        owner_perms["avatar_url"] = staff_avatar_url(OWNER_EMAIL)
        owner_staff = StaffMember(
            tenant_id=tenant.id,
            branch_id=branch.id,
            name="Dr. Neema Mwangi",
            email=OWNER_EMAIL,
            phone="+255712345678",
            role=StaffRole.owner,
            permissions=owner_perms,
        )
        db.add(owner_staff)
        await db.flush()

        db.add(
            User(
                email=OWNER_EMAIL,
                hashed_password=hashed,
                name="Dr. Neema Mwangi",
                phone="+255712345678",
                role=UserRole.vendor_owner,
                tenant_id=tenant.id,
                staff_id=owner_staff.id,
            )
        )

        pharm_perms = dict(DEFAULT_PERMISSIONS.get("Pharmacist", DEFAULT_PERMISSIONS["Cashier"]))
        pharm_perms["avatar_url"] = staff_avatar_url(PHARMACIST_EMAIL)
        pharmacist = StaffMember(
            tenant_id=tenant.id,
            branch_id=branch.id,
            name="Pharmacist — Neema",
            email=PHARMACIST_EMAIL,
            phone="+255713000111",
            role=StaffRole.pharmacist,
            permissions=pharm_perms,
        )
        db.add(pharmacist)
        await db.flush()

        db.add(
            User(
                email=PHARMACIST_EMAIL,
                hashed_password=hashed,
                name="Pharmacist — Neema",
                phone="+255713000111",
                role=UserRole.vendor_staff,
                tenant_id=tenant.id,
                staff_id=pharmacist.id,
            )
        )

        rx_products: list[Product] = []
        now = datetime.now(UTC)

        for idx in range(PRODUCT_COUNT):
            base = PHARMACY_SKU_BASE[idx % len(PHARMACY_SKU_BASE)]
            pname, category, price, cost, stock_base, unit, requires_rx = base
            if idx >= len(PHARMACY_SKU_BASE):
                pname = f"{pname} — Line {idx + 1}"
            sku = f"PHM-{idx + 1:03d}"
            stock_var = max(0, int(stock_base * (0.4 + (idx % 9) * 0.08)))
            reorder = max(5, int(stock_base * 0.25))
            # Spread expiry: some expired, many within 30–90 days, rest 6–18 months
            expiry_roll = idx % 10
            if expiry_roll == 0:
                expiry = now - timedelta(days=rng.randint(5, 45))
            elif expiry_roll <= 3:
                expiry = now + timedelta(days=rng.randint(7, 28))
            elif expiry_roll <= 6:
                expiry = now + timedelta(days=rng.randint(29, 75))
            else:
                expiry = now + timedelta(days=rng.randint(120, 540))

            if idx % 11 == 0:
                stock_var = max(0, reorder - rng.randint(1, 4))  # low stock
            if idx % 17 == 0:
                stock_var = 0

            batch = f"B{now.year % 100}{idx + 1:04d}"

            product = Product(
                tenant_id=tenant.id,
                branch_id=branch.id,
                name=pname,
                category=category,
                sku=sku,
                barcode=f"628100{idx:06d}",
                price=round(price * (0.95 + (idx % 5) * 0.02), 0),
                cost=cost,
                stock=float(stock_var),
                reorder_point=float(reorder),
                unit=unit,
                batch_number=batch,
                expiry_date=expiry,
                requires_prescription=requires_rx or (idx % 13 == 0 and category == "Antibiotics"),
                business_type=BusinessType.pharmacy,
                metadata_json={
                    "image_url": ai_pharmacy_product_image_url(pname, sku, idx % 3),
                    "is_drug": category not in ("Cosmetics", "Devices", "First Aid"),
                },
            )
            db.add(product)
            if product.requires_prescription:
                rx_products.append(product)

        await db.flush()

        for cidx, (cname, phone) in enumerate(
            [
                ("Salum Omar", "+255754111222"),
                ("Grace Temba", "+255684333444"),
                ("Hospital Ward 3", "+255222000111"),
            ]
        ):
            db.add(
                Customer(
                    tenant_id=tenant.id,
                    branch_id=branch.id,
                    name=cname,
                    phone=phone,
                    credit_limit=200000,
                    balance=0,
                )
            )

        await db.flush()

        sample_rx_drugs = rx_products[:8] if rx_products else []
        statuses = [
            PrescriptionStatus.pending,
            PrescriptionStatus.pending,
            PrescriptionStatus.approved,
            PrescriptionStatus.dispensed,
            PrescriptionStatus.rejected,
        ]
        doctors = [
            ("Dr. Juma Mwakyoma", "MDT-8821"),
            ("Dr. Asha Kimaro", "MDT-4412"),
            ("Dr. Peter Lyimo", "MDT-9901"),
        ]
        for ridx, prod in enumerate(sample_rx_drugs):
            doc, lic = doctors[ridx % len(doctors)]
            db.add(
                Prescription(
                    tenant_id=tenant.id,
                    branch_id=branch.id,
                    product_id=prod.id,
                    product_name=prod.name,
                    patient_name=f"Patient {ridx + 1}",
                    customer_phone=f"+255754{ridx:06d}",
                    doctor_name=doc,
                    doctor_license=lic,
                    prescription_number=f"RX-{now.year}-{1000 + ridx}",
                    quantity_requested=float(rng.randint(1, 3)),
                    status=statuses[ridx % len(statuses)],
                    notes="TMDA registered prescriber reference on file." if ridx % 2 == 0 else None,
                    verified_by_name=pharmacist.name if statuses[ridx % len(statuses)] != PrescriptionStatus.pending else None,
                    verified_by_user_id=None,
                )
            )

        await db.commit()

    logger.info("Minimal pharmacy demo seeded: %s (%s products)", OWNER_EMAIL, PRODUCT_COUNT)
    return {"owner": OWNER_EMAIL, "pharmacist": PHARMACIST_EMAIL, "password": DEMO_PASSWORD}
