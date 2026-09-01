#!/usr/bin/env python3
"""Run database seed manually: python scripts/run_seed.py"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.database import init_db
from app.seed import ensure_super_admin, seed_demo_data
from app.seed_sample_data import DEMO_PASSWORD, SEED_MARKER
from sqlalchemy import func, select
from app.database import AsyncSessionLocal
from app.models import Product, Sale, Tenant, User


async def main() -> None:
    await init_db()
    admin = await ensure_super_admin()
    print(f"Super admin bootstrap: {admin}")
    await seed_demo_data()

    async with AsyncSessionLocal() as db:
        tenants = await db.scalar(
            select(func.count(Tenant.id)).where(Tenant.owner_email.like(f"%{SEED_MARKER}"))
        ) or 0
        users = await db.scalar(
            select(func.count(User.id)).where(User.email.like(f"%{SEED_MARKER}"))
        ) or 0
        products = await db.scalar(select(func.count(Product.id))) or 0
        sales = await db.scalar(select(func.count(Sale.id))) or 0

    print("Seed complete.")
    print(f"  Sample tenants: {tenants}")
    print(f"  Sample users:   {users}")
    print(f"  Products:       {products}")
    print(f"  Sales:          {sales}")
    print()
    print(f"Super admin: {settings.super_admin_email} / (your SUPER_ADMIN_PASSWORD)")
    print(f"Demo password for all sample accounts: {DEMO_PASSWORD}")
    print()
    print("Quick logins (6 business types):")
    for alias in [
        "pharmacy@sample.dukaplus.co.tz",
        "retail@sample.dukaplus.co.tz",
        "restaurant@sample.dukaplus.co.tz",
        "hardware@sample.dukaplus.co.tz",
        "electronics@sample.dukaplus.co.tz",
        "supermarket@sample.dukaplus.co.tz",
    ]:
        print(f"  {alias}")
    print()
    print("Staff roles per shop: manager.{slug}, cashier.{slug}@sample.dukaplus.co.tz")
    print("Example: manager.kariakoo-pharmacy@sample.dukaplus.co.tz / demo123")


if __name__ == "__main__":
    asyncio.run(main())
