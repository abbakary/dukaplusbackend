"""Demo starter data for newly registered tenants (products + calendar walkthrough)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BusinessType, CalendarEvent, Product, Tenant


async def seed_tenant_starter_pack(
    db: AsyncSession,
    *,
    tenant: Tenant,
    branch_id: str,
    owner_name: str,
) -> None:
    biz = tenant.business_type if tenant.business_type else BusinessType.retail
    today = datetime.now(UTC).date()

    demo_products = [
        ("DEMO-001", "Cement 50kg (sample)", "Building", 18500, 15200, 48),
        ("DEMO-002", "Paint 20L — Premium white", "Finishing", 89000, 72000, 12),
        ("DEMO-003", "Iron sheets gauge 30", "Roofing", 42000, 35500, 25),
    ]
    for sku, name, cat, price, cost, stock in demo_products:
        db.add(
            Product(
                tenant_id=tenant.id,
                branch_id=branch_id,
                name=name,
                category=cat,
                sku=sku,
                price=float(price),
                cost=float(cost),
                stock=float(stock),
                reorder_point=10,
                business_type=biz,
                metadata_json={"showcase": True, "starter_pack": True},
            )
        )

    events = [
        (0, "Receive stock — demo delivery", "delivery", "10:00", "high", "Count cartons and post goods received in Inventory."),
        (2, "TRA VAT return reminder", "compliance", "14:00", "high", "Review EFD receipts before filing on TRA portal."),
        (4, "Customer credit follow-up", "dunning", "11:00", "medium", "Call top debtors from Receivables module."),
        (7, "Staff shift handover", "shift", "08:00", "medium", f"Assign cashier roster — lead: {owner_name}"),
        (10, "Weekend promo push", "promo", "09:30", "low", "Prepare POS discounts and shelf labels."),
        (14, "Store maintenance window", "maintenance", "18:00", "low", "Backup data and check printers."),
    ]
    for day_offset, title, category, time, priority, description in events:
        ev_date = (today + timedelta(days=day_offset)).isoformat()
        db.add(
            CalendarEvent(
                tenant_id=tenant.id,
                branch_id=branch_id,
                title=title,
                category=category,
                event_date=ev_date,
                event_time=time,
                priority=priority,
                description=description,
                assigned_to=owner_name,
                metadata_json={"starter_pack": True},
            )
        )

    await db.flush()
