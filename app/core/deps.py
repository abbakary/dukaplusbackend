from datetime import datetime
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import decode_token
from app.database import get_db
from app.models import StaffMember, Tenant, User, UserRole

security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    payload = decode_token(credentials.credentials)
    if not payload or payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")
    result = await db.execute(
        select(User)
        .options(selectinload(User.tenant), selectinload(User.staff))
        .where(User.id == user_id, User.is_active == True)  # noqa: E712
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


async def get_optional_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User | None:
    if not credentials:
        return None
    try:
        return await get_current_user(credentials, db)
    except HTTPException:
        return None


def require_roles(*roles: UserRole):
    async def checker(user: Annotated[User, Depends(get_current_user)]) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return user
    return checker


def require_tenant(user: User) -> str:
    if not user.tenant_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No tenant associated")
    return user.tenant_id


def get_user_permissions(user: User) -> dict[str, bool]:
    if user.role == UserRole.super_admin:
        return {k: True for k in [
            "canSellPOS", "canGiveCredit", "canModifyInventory", "canViewProfitReports",
            "canManageSuppliers", "canApproveDiscounts", "canVoidReceipts",
            "canPerformDailyClosing", "canAccessSuperAdmin",
        ]}
    if user.role == UserRole.vendor_owner:
        from app.core.security import DEFAULT_PERMISSIONS
        return DEFAULT_PERMISSIONS["Owner"]
    if user.staff and user.staff.permissions:
        return user.staff.permissions
    if user.staff:
        from app.core.security import DEFAULT_PERMISSIONS
        return DEFAULT_PERMISSIONS.get(user.staff.role.value, DEFAULT_PERMISSIONS["Cashier"])
    from app.core.security import DEFAULT_PERMISSIONS
    return DEFAULT_PERMISSIONS["Owner"]


def require_permission(permission: str):
    async def checker(user: Annotated[User, Depends(get_current_user)]) -> User:
        perms = get_user_permissions(user)
        if not perms.get(permission, False):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Missing permission: {permission}")
        return user
    return checker
