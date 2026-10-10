"""Normalize staff.permissions JSON — RBAC booleans only for auth; payroll lives elsewhere."""

from __future__ import annotations

from typing import Any

# Stored on staff.permissions but not exposed as RBAC flags on /auth/me
STAFF_PERMISSION_META_KEYS = frozenset({"avatar_url", "payroll_profile"})

KNOWN_RBAC_KEYS = frozenset(
    {
        "canSellPOS",
        "canGiveCredit",
        "canModifyInventory",
        "canViewInventory",
        "canViewProfitReports",
        "canManageSuppliers",
        "canApproveDiscounts",
        "canOverridePrices",
        "canVoidReceipts",
        "canPerformDailyClosing",
        "canAccessSuperAdmin",
    }
)


def extract_payroll_profile(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if not raw:
        return None
    profile = raw.get("payroll_profile")
    return profile if isinstance(profile, dict) else None


def sanitize_rbac_permissions(raw: dict[str, Any] | None) -> dict[str, bool]:
    """Return bool permission flags safe for UserResponse.permissions."""
    if not raw:
        return {}
    out: dict[str, bool] = {}
    for key, value in raw.items():
        if key in STAFF_PERMISSION_META_KEYS:
            continue
        if isinstance(value, bool):
            out[key] = value
        elif key in KNOWN_RBAC_KEYS:
            out[key] = bool(value)
        elif key.startswith("can") and not isinstance(value, (dict, list)):
            out[key] = bool(value)
    return out


async def upsert_payroll_contract_from_profile(session, staff, profile: dict[str, Any]) -> None:
    """Save HR payroll profile to hr_payroll_contracts (not staff.permissions)."""
    import json

    from sqlalchemy import select

    from app.models.accounting import HrPayrollContract

    try:
        wage = float(profile.get("baseSalary") or profile.get("wage_monthly") or 0)
    except (TypeError, ValueError):
        wage = 0.0
    result = await session.execute(
        select(HrPayrollContract).where(
            HrPayrollContract.tenant_id == staff.tenant_id,
            HrPayrollContract.staff_id == staff.id,
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        row = HrPayrollContract(tenant_id=staff.tenant_id, staff_id=staff.id)
        session.add(row)
    row.staff_name = staff.name
    if wage > 0:
        row.wage_monthly = wage
    row.nssf_enabled = bool(profile.get("nssfEnabled", True))
    row.paye_enabled = bool(profile.get("payeEnabled", True))
    row.active = staff.active
    row.profile_json = json.dumps(profile)
    await session.flush()


def normalize_staff_permissions_blob(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Persisted staff.permissions: RBAC booleans + optional avatar_url (+ legacy payroll until migrated)."""
    if not raw:
        return {}
    rbac = sanitize_rbac_permissions(raw)
    out: dict[str, Any] = dict(rbac)
    avatar = raw.get("avatar_url")
    if isinstance(avatar, str) and avatar.strip():
        out["avatar_url"] = avatar.strip()
    return out
