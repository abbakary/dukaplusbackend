"""Staff role storage: legacy lowercase / PG enum labels → canonical Title Case values."""

from __future__ import annotations

import enum

from sqlalchemy import String
from sqlalchemy.types import TypeDecorator


class StaffRole(str, enum.Enum):
    owner = "Owner"
    manager = "Manager"
    hr = "HR"
    pharmacist = "Pharmacist"
    cashier = "Cashier"
    storekeeper = "Storekeeper"
    accountant = "Accountant"

    @classmethod
    def _missing_(cls, value: object):
        if not isinstance(value, str):
            return None
        raw = value.strip()
        if not raw:
            return None
        for member in cls:
            if member.value == raw or member.name == raw.lower():
                return member
        lowered = raw.lower()
        for member in cls:
            if member.value.lower() == lowered or member.name == lowered:
                return member
        return None


def coerce_staff_role(value: str | StaffRole) -> StaffRole:
    if isinstance(value, StaffRole):
        return value
    parsed = StaffRole(value)
    if parsed is not None:
        return parsed
    raise ValueError(f"Invalid staff role: {value!r}")


# DB rows may still hold legacy lowercase enum labels after VARCHAR conversion.
_LEGACY_DB_TO_CANONICAL: dict[str, str] = {
    "owner": StaffRole.owner.value,
    "manager": StaffRole.manager.value,
    "hr": StaffRole.hr.value,
    "pharmacist": StaffRole.pharmacist.value,
    "cashier": StaffRole.cashier.value,
    "storekeeper": StaffRole.storekeeper.value,
    "accountant": StaffRole.accountant.value,
}


def canonical_staff_role_db_value(value: str | StaffRole) -> str:
    if isinstance(value, StaffRole):
        return value.value
    text = value.strip()
    if text in {m.value for m in StaffRole}:
        return text
    legacy = _LEGACY_DB_TO_CANONICAL.get(text.lower())
    if legacy:
        return legacy
    return coerce_staff_role(text).value


class StaffRoleType(TypeDecorator):
    """VARCHAR column; accepts legacy lowercase labels from PostgreSQL staffrole enum."""

    impl = String(32)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return canonical_staff_role_db_value(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return coerce_staff_role(str(value))
