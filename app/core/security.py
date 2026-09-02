import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import RefreshToken, User

ALGORITHM = "HS256"


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(UTC) + (
        expires_delta or timedelta(days=settings.access_token_expire_days)
    )
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, settings.secret_key, algorithm=ALGORITHM)


def create_refresh_token_value() -> str:
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    return bcrypt.hashpw(token.encode(), bcrypt.gensalt()).decode()


def verify_token_hash(token: str, token_hash: str) -> bool:
    return bcrypt.checkpw(token.encode(), token_hash.encode())


def decode_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError:
        return None


async def store_refresh_token(
    db: AsyncSession,
    user_id: str,
    token: str,
    device_info: str | None = None,
) -> RefreshToken:
    refresh = RefreshToken(
        user_id=user_id,
        token_hash=hash_token(token),
        expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days),
        device_info=device_info,
    )
    db.add(refresh)
    await db.flush()
    return refresh


async def revoke_refresh_token(db: AsyncSession, token: str) -> bool:
    result = await db.execute(select(RefreshToken).where(RefreshToken.revoked == False))  # noqa: E712
    tokens = result.scalars().all()
    for stored in tokens:
        if verify_token_hash(token, stored.token_hash):
            stored.revoked = True
            return True
    return False


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    normalized = email.strip().lower()
    result = await db.execute(select(User).where(User.email == normalized))
    return result.scalar_one_or_none()


DEFAULT_PERMISSIONS: dict[str, dict[str, bool]] = {
    "Owner": {
        "canSellPOS": True, "canGiveCredit": True, "canModifyInventory": True,
        "canViewProfitReports": True, "canManageSuppliers": True, "canApproveDiscounts": True,
        "canOverridePrices": True, "canVoidReceipts": True, "canPerformDailyClosing": True,
        "canAccessSuperAdmin": False,
    },
    "Manager": {
        "canSellPOS": True, "canGiveCredit": True, "canModifyInventory": True,
        "canViewProfitReports": True, "canManageSuppliers": True, "canApproveDiscounts": True,
        "canOverridePrices": True, "canVoidReceipts": True, "canPerformDailyClosing": True,
        "canAccessSuperAdmin": False,
    },
    "Pharmacist": {
        "canSellPOS": True, "canGiveCredit": True, "canModifyInventory": True,
        "canViewProfitReports": False, "canManageSuppliers": True, "canApproveDiscounts": True,
        "canOverridePrices": True, "canVoidReceipts": True, "canPerformDailyClosing": False,
        "canAccessSuperAdmin": False,
    },
    "Cashier": {
        "canSellPOS": True, "canGiveCredit": False, "canModifyInventory": False,
        "canViewProfitReports": False, "canManageSuppliers": False, "canApproveDiscounts": False,
        "canOverridePrices": False, "canVoidReceipts": False, "canPerformDailyClosing": True,
        "canAccessSuperAdmin": False,
    },
    "Storekeeper": {
        "canSellPOS": False, "canGiveCredit": False, "canModifyInventory": True,
        "canViewProfitReports": False, "canManageSuppliers": True, "canApproveDiscounts": False,
        "canOverridePrices": False, "canVoidReceipts": False, "canPerformDailyClosing": False,
        "canAccessSuperAdmin": False,
    },
    "Accountant": {
        "canSellPOS": False, "canGiveCredit": True, "canModifyInventory": True,
        "canViewProfitReports": True, "canManageSuppliers": True, "canApproveDiscounts": False,
        "canOverridePrices": False, "canVoidReceipts": False, "canPerformDailyClosing": True,
        "canAccessSuperAdmin": False,
    },
}
