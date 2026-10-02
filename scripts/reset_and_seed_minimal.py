#!/usr/bin/env python3
"""Wipe DB and seed: super admin + 1 pharmacy (105 AI-image products, expiry, Rx queue).

Usage (from backend/):
  python scripts/reset_and_seed_minimal.py

Then start API — super admin is created on startup via ensure_super_admin().
Set SUPER_ADMIN_EMAIL / SUPER_ADMIN_PASSWORD in .env as needed.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.seed import ensure_super_admin
from app.seed_minimal_pharmacy import seed_minimal_pharmacy_demo


async def main() -> None:
    print("Wiping database and seeding minimal pharmacy demo…")
    print(f"Database: {settings.database_url}")
    result = await seed_minimal_pharmacy_demo(skip_wipe=False)
    admin = await ensure_super_admin()
    print()
    print("Done.")
    print(f"  Super admin: {admin.get('email')} / (SUPER_ADMIN_PASSWORD in .env, default admin123)")
    print(f"  Pharmacy owner: {result['owner']} / {result['password']}")
    print(f"  Pharmacist staff: {result['pharmacist']} / {result['password']}")
    print(f"  Products: 105 with AI images, batch/expiry, low-stock & Rx flags")


if __name__ == "__main__":
    asyncio.run(main())
