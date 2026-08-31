from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_user_permissions
from app.core.security import (
    create_access_token,
    create_refresh_token_value,
    get_user_by_email,
    revoke_refresh_token,
    store_refresh_token,
    verify_password,
)
from app.database import get_db
from app.models import (
    TenantStatus,
    User,
)
from app.schemas import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from app.services.account_service import create_tenant_with_owner

router = APIRouter(prefix="/auth", tags=["auth"])


def _build_user_response(user: User) -> UserResponse:
    tenant = user.tenant
    staff = user.staff
    status_val = "approved"
    if tenant and tenant.status == TenantStatus.pending_kyc:
        status_val = "pending"
    elif tenant and tenant.status == TenantStatus.suspended:
        status_val = "rejected"

    return UserResponse(
        id=user.id,
        name=user.name,
        email=user.email,
        phone=user.phone,
        role=user.role.value,
        tenant_id=user.tenant_id,
        business_name=tenant.name if tenant else None,
        business_type=tenant.business_type.value if tenant else None,
        staff_role=staff.role.value if staff else None,
        staff_id=staff.id if staff else None,
        branch=None,
        permissions=get_user_permissions(user),
        language=user.language,
        status=status_val,
    )


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: Annotated[AsyncSession, Depends(get_db)]):
    user = await get_user_by_email(db, body.email)
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")

    user.last_login = datetime.now(UTC)
    access = create_access_token({"sub": user.id, "role": user.role.value, "tenant_id": user.tenant_id})
    refresh_val = create_refresh_token_value()
    await store_refresh_token(db, user.id, refresh_val, body.device_info)

    return TokenResponse(access_token=access, refresh_token=refresh_val)


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, db: Annotated[AsyncSession, Depends(get_db)]):
    _, user = await create_tenant_with_owner(db, body)
    return _build_user_response(user)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(body: RefreshRequest, db: Annotated[AsyncSession, Depends(get_db)]):
    from sqlalchemy import select
    from app.models import RefreshToken
    from app.core.security import verify_token_hash

    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.revoked == False,  # noqa: E712
            RefreshToken.expires_at > datetime.now(UTC),
        )
    )
    tokens = result.scalars().all()
    matched = None
    for stored in tokens:
        if verify_token_hash(body.refresh_token, stored.token_hash):
            matched = stored
            break
    if not matched:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    user_result = await db.execute(select(User).where(User.id == matched.user_id))
    user = user_result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    matched.revoked = True
    access = create_access_token({"sub": user.id, "role": user.role.value, "tenant_id": user.tenant_id})
    refresh_val = create_refresh_token_value()
    await store_refresh_token(db, user.id, refresh_val, matched.device_info)

    return TokenResponse(access_token=access, refresh_token=refresh_val)


@router.post("/logout")
async def logout(
    body: RefreshRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    await revoke_refresh_token(db, body.refresh_token)
    return {"message": "Logged out successfully"}


@router.get("/me", response_model=UserResponse)
async def get_me(user: Annotated[User, Depends(get_current_user)]):
    return _build_user_response(user)
