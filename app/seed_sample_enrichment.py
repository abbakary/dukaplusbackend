"""Backfill sample tenants with demo images, accounting journals, and extra branches."""

from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy import func, select

from app.demo_media import ai_product_image_url, product_image_url, staff_avatar_url
from app.database import AsyncSessionLocal
from app.models import Branch, Product, StaffMember, Tenant
from app.models.accounting import JournalEntry, JournalLine
from app.seed_sample_data import SEED_MARKER
from app.services.accounting_defaults import ensure_default_chart

logger = logging.getLogger(__name__)

ENRICHMENT_VERSION = "2026-03-21-v1"


async def enrich_sample_tenants() -> dict[str, int]:
    """Idempotent: images on products/staff, opening journals, Mbezi branch for retail demo."""
    stats = {"products": 0, "staff": 0, "journals": 0, "branches": 0}
    async with AsyncSessionLocal() as db:
        tenants = (
            await db.execute(
                select(Tenant).where(Tenant.owner_email.like(f"%{SEED_MARKER}"))
            )
        ).scalars().all()

        for tenant in tenants:
            biz_type = tenant.business_type

            prod_rows = (
                await db.execute(select(Product).where(Product.tenant_id == tenant.id))
            ).scalars().all()
            for pidx, product in enumerate(prod_rows):
                meta = dict(product.metadata_json or {})
                if not meta.get("image_url") and not meta.get("imageUrl"):
                    use_ai = "jengo-mega-hardware" in (tenant.owner_email or "")
                    if use_ai:
                        meta["image_url"] = ai_product_image_url(
                            product.name or "hardware item",
                            product.sku or product.id,
                            pidx,
                        )
                        meta["image_source"] = "ai_pollinations"
                    else:
                        meta["image_url"] = product_image_url(
                            biz_type, product.sku or product.id, pidx,
                        )
                    product.metadata_json = meta
                    stats["products"] += 1

            staff_rows = (
                await db.execute(select(StaffMember).where(StaffMember.tenant_id == tenant.id))
            ).scalars().all()
            for staff in staff_rows:
                perms = dict(staff.permissions or {})
                if not perms.get("avatar_url"):
                    perms["avatar_url"] = staff_avatar_url(staff.email or staff.id)
                    staff.permissions = perms
                    stats["staff"] += 1

            entry_count = await db.scalar(
                select(func.count(JournalEntry.id)).where(JournalEntry.tenant_id == tenant.id)
            )
            if not entry_count:
                accounts = await ensure_default_chart(db, tenant.id)
                by_code = {a.code: a for a in accounts}
                branch = (
                    await db.execute(
                        select(Branch).where(Branch.tenant_id == tenant.id).limit(1)
                    )
                ).scalar_one_or_none()
                branch_id = branch.id if branch else None
                today = date.today()
                opening = JournalEntry(
                    tenant_id=tenant.id,
                    branch_id=branch_id,
                    entry_date=today - timedelta(days=30),
                    reference="OPEN-2026",
                    memo="Demo opening balances — cash, inventory, equity",
                    source="seed",
                )
                db.add(opening)
                await db.flush()
                for acct_code, debit, credit, label in [
                    ("1000", 2_500_000, 0, "Cash & mobile float"),
                    ("1300", 4_800_000, 0, "Opening inventory"),
                    ("3000", 0, 7_300_000, "Owner capital"),
                ]:
                    acct = by_code.get(acct_code)
                    if not acct:
                        continue
                    db.add(
                        JournalLine(
                            entry_id=opening.id,
                            account_id=acct.id,
                            label=label,
                            debit=debit,
                            credit=credit,
                        )
                    )
                sales_entry = JournalEntry(
                    tenant_id=tenant.id,
                    branch_id=branch_id,
                    entry_date=today - timedelta(days=7),
                    reference="SALE-DEMO-001",
                    memo="Sample POS revenue with VAT (TRA books)",
                    source="seed",
                )
                db.add(sales_entry)
                await db.flush()
                for acct_code, debit, credit, label in [
                    ("1000", 590_000, 0, "M-Pesa & cash takings"),
                    ("4000", 0, 500_000, "Retail sales"),
                    ("2100", 0, 90_000, "VAT 18% payable"),
                ]:
                    acct = by_code.get(acct_code)
                    if not acct:
                        continue
                    db.add(
                        JournalLine(
                            entry_id=sales_entry.id,
                            account_id=acct.id,
                            label=label,
                            debit=debit,
                            credit=credit,
                        )
                    )
                stats["journals"] += 2

            if tenant.owner_email.startswith("owner.mbezi-retail@"):
                existing = (
                    await db.execute(
                        select(Branch).where(
                            Branch.tenant_id == tenant.id,
                            Branch.name.ilike("%Mbezi Beach%"),
                        )
                    )
                ).scalar_one_or_none()
                if not existing:
                    hq = (
                        await db.execute(
                            select(Branch).where(Branch.tenant_id == tenant.id).limit(1)
                        )
                    ).scalar_one_or_none()
                    db.add(
                        Branch(
                            tenant_id=tenant.id,
                            name="Mbezi Beach — Branch",
                            code="MBZ01",
                            branch_type="branch",
                            status="active",
                            region=hq.region if hq else "Dar es Salaam",
                            district="Kinondoni",
                            address="Mbezi Beach, Dar es Salaam",
                            phone=hq.phone if hq else "+255712345678",
                            tra_efd_serial=tenant.tra_efd_serial or "",
                            opening_hours="08:00 - 21:00",
                        )
                    )
                    stats["branches"] += 1

        await db.commit()

    if any(stats.values()):
        logger.info("Sample tenant enrichment %s: %s", ENRICHMENT_VERSION, stats)
    return stats
