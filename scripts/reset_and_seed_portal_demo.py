"""Wipe local SQLite DB and seed the 3-tenant portal demo (50+ rows per module).

Usage (from backend/):
  python scripts/reset_and_seed_portal_demo.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def main() -> None:
    from app.seed import ensure_super_admin
    from app.seed_minimal_pharmacy import wipe_all_tables
    from app.seed_portal_demo import seed_portal_demo

    print("Wiping database and recreating tables…")
    await wipe_all_tables()
    print("Creating super admin…")
    await ensure_super_admin()
    print("Seeding portal demo (pharmacy, hardware, retail)…")
    result = await seed_portal_demo()
    print("Done.")
    print("Super admin:", "admin@dukaplus.co.tz (see SUPER_ADMIN_PASSWORD in .env)")
    print("Demo password:", result.get("password", "demo123"))
    for email in result.get("owners", []):
        print("  Owner login:", email)


if __name__ == "__main__":
    asyncio.run(main())
