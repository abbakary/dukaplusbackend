"""Bootstrap data — super admin + optional rich sample dataset."""

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.core.security import hash_password
from app.database import AsyncSessionLocal
from app.models import User, UserRole
from app.seed_sample_data import seed_login_aliases, seed_sample_data

logger = logging.getLogger(__name__)


def _normalize_email(email: str) -> str:
    return email.strip().lower()


async def ensure_super_admin() -> dict[str, str | bool]:
    """Create or refresh the platform super admin (idempotent, Railway-safe).

    - Creates the account when missing
    - When ``super_admin_sync_password`` is true, updates password from env on each startup
    - Handles concurrent worker inserts on deploy
    """
    email = _normalize_email(settings.super_admin_email)
    if not email:
        logger.error("SUPER_ADMIN_EMAIL is empty — super admin was not created")
        return {"email": "", "created": False, "password_synced": False, "error": "empty_email"}

    hashed = hash_password(settings.super_admin_password)
    created = False
    password_synced = False

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

        if user is None:
            db.add(User(
                email=email,
                hashed_password=hashed,
                name=settings.super_admin_name,
                phone=settings.super_admin_phone,
                role=UserRole.super_admin,
                is_active=True,
            ))
            try:
                await db.commit()
                created = True
                logger.info("Created super admin account: %s", email)
            except IntegrityError:
                await db.rollback()
                result = await db.execute(select(User).where(User.email == email))
                user = result.scalar_one_or_none()
                logger.info("Super admin already created by another worker: %s", email)
        else:
            changed = False
            if user.role != UserRole.super_admin:
                user.role = UserRole.super_admin
                changed = True
            if not user.is_active:
                user.is_active = True
                changed = True
            if settings.super_admin_sync_password:
                user.hashed_password = hashed
                password_synced = True
                changed = True
            if user.name != settings.super_admin_name:
                user.name = settings.super_admin_name
                changed = True
            if changed:
                await db.commit()
                if password_synced:
                    logger.info("Synced super admin password from env for: %s", email)
                else:
                    logger.info("Updated super admin profile for: %s", email)
            else:
                logger.info("Super admin already up to date: %s", email)

    return {
        "email": email,
        "created": created,
        "password_synced": password_synced or created,
    }


async def seed_demo_data() -> None:
    """Create platform super admin and optional sample tenants (idempotent)."""
    await ensure_super_admin()

    if settings.seed_demo_data:
        await seed_sample_data()
        await seed_login_aliases()
