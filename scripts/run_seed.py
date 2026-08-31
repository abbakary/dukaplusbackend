#!/usr/bin/env python3
"""Run database seed manually: python scripts/run_seed.py"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import init_db
from app.seed import seed_demo_data


async def main() -> None:
    await init_db()
    await seed_demo_data()
    print("Seed complete.")
    print("Super admin: admin@dukaplus.co.tz / admin123")
    print("Sample owners: owner.{slug}@sample.dukaplus.co.tz / demo123")
    print("Short aliases: pharmacy@sample.dukaplus.co.tz, retail@sample.dukaplus.co.tz, demo@sample.dukaplus.co.tz / demo123")


if __name__ == "__main__":
    asyncio.run(main())
