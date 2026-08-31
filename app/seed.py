"""Bootstrap data — super admin + optional rich sample dataset."""

from sqlalchemy import select

from app.config import settings
from app.core.security import hash_password
from app.database import AsyncSessionLocal
from app.models import User, UserRole
from app.seed_sample_data import seed_login_aliases, seed_sample_data


async def seed_demo_data() -> None:
    """Create platform super admin and optional sample tenants (idempotent)."""
    async with AsyncSessionLocal() as db:
        existing_admin = await db.execute(
            select(User).where(User.email == settings.super_admin_email)
        )
        if not existing_admin.scalar_one_or_none():
            db.add(User(
                email=settings.super_admin_email,
                hashed_password=hash_password(settings.super_admin_password),
                name=settings.super_admin_name,
                phone=settings.super_admin_phone,
                role=UserRole.super_admin,
            ))
        await db.commit()

    if settings.seed_demo_data and not settings.is_production:
        await seed_sample_data()
        await seed_login_aliases()
