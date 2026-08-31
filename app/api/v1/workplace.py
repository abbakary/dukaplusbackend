"""Dynamic workplace state — restaurant tables/KOT, service appointments."""

from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.business_profiles import get_business_profile
from app.core.deps import get_current_user, require_tenant
from app.database import get_db
from app.models import Branch, TenantWorkplaceState, User

router = APIRouter(prefix="/workplace", tags=["workplace"])

NOW = lambda: datetime.now(timezone.utc).isoformat()
DEFAULT_BRANCH_KEY = "hq"


def _default_tables() -> list[dict[str, Any]]:
    return [
        {
            "id": f"T{i}",
            "label": f"Table {i}",
            "seats": 4 if i <= 8 else 6,
            "status": "available",
            "order_total": 0,
            "guest_count": 0,
            "assigned_waiter": "",
            "seated_at": None,
            "items": [],
        }
        for i in range(1, 13)
    ]


def _branch_default_state() -> dict[str, Any]:
    return {
        "tables": _default_tables(),
        "orders": [],
        "reservations": [],
        "staff_performance": [],
        "kots": [],
    }


DEFAULT_STATE: dict[str, Any] = {
    "by_branch": {},
    **_branch_default_state(),
}


class WorkplaceStateUpdate(BaseModel):
    tables: list[dict[str, Any]] | None = None
    orders: list[dict[str, Any]] | None = None
    kots: list[dict[str, Any]] | None = None
    reservations: list[dict[str, Any]] | None = None
    staff_performance: list[dict[str, Any]] | None = None
    appointments: list[dict[str, Any]] | None = None


def _migrate_legacy_kots(branch_state: dict[str, Any]) -> dict[str, Any]:
    if branch_state.get("orders"):
        return branch_state
    kots = branch_state.get("kots") or []
    if not kots:
        branch_state["orders"] = []
        return branch_state
    orders = []
    status_map = {
        "pending": "new",
        "preparing": "cooking",
        "ready": "ready",
        "served": "served",
    }
    for kot in kots:
        raw_status = kot.get("status", "pending")
        orders.append(
            {
                "id": kot.get("id", f"KOT-{len(orders)+1}"),
                "table_id": kot.get("table_id"),
                "counter_label": None,
                "items": [{"name": line, "qty": 1, "price": 0} for line in kot.get("items", [])],
                "status": status_map.get(raw_status, "new"),
                "urgent_note": "",
                "waiter_name": "",
                "created_at": kot.get("created_at", NOW()),
                "status_history": [{"status": status_map.get(raw_status, "new"), "at": NOW()}],
            }
        )
    branch_state["orders"] = orders
    return branch_state


def _ensure_restaurant_defaults(branch_state: dict[str, Any]) -> dict[str, Any]:
    defaults = _branch_default_state()
    branch_state.setdefault("orders", [])
    branch_state.setdefault("reservations", defaults["reservations"])
    branch_state.setdefault("staff_performance", defaults["staff_performance"])
    if not branch_state.get("tables"):
        branch_state["tables"] = _default_tables()
    return branch_state


def _normalize_root_state(state: dict[str, Any], business_type: str) -> dict[str, Any]:
    """Migrate flat tenant state into by_branch map."""
    if "by_branch" not in state or not isinstance(state.get("by_branch"), dict):
        legacy = {
            k: state[k]
            for k in ("tables", "orders", "kots", "reservations", "staff_performance", "appointments")
            if k in state
        }
        if not legacy:
            legacy = _branch_default_state() if business_type == "restaurant" else {"tables": [], "orders": [], "kots": [], "appointments": []}
        state = {"by_branch": {DEFAULT_BRANCH_KEY: legacy}, "appointments": legacy.get("appointments", [])}
    return state


def _resolve_branch_key(branch_id: str | None, tenant_branches: list[Branch]) -> str:
    if branch_id and any(b.id == branch_id for b in tenant_branches):
        return branch_id
    if tenant_branches:
        hq = next((b for b in tenant_branches if b.branch_type == "main_hq"), tenant_branches[0])
        return hq.id
    return branch_id or DEFAULT_BRANCH_KEY


def _get_branch_slice(state: dict[str, Any], branch_key: str, business_type: str) -> dict[str, Any]:
    by_branch = state.setdefault("by_branch", {})
    if branch_key not in by_branch:
        # Seed from legacy flat keys or defaults
        if state.get("tables") or state.get("orders"):
            by_branch[branch_key] = {
                "tables": state.get("tables", []),
                "orders": state.get("orders", []),
                "kots": state.get("kots", []),
                "reservations": state.get("reservations", []),
                "staff_performance": state.get("staff_performance", []),
            }
        else:
            by_branch[branch_key] = _branch_default_state() if business_type == "restaurant" else {
                "tables": [], "orders": [], "kots": [], "reservations": [], "staff_performance": [],
            }
    branch_state = dict(by_branch[branch_key])
    branch_state = _migrate_legacy_kots(branch_state)
    if business_type == "restaurant":
        branch_state = _ensure_restaurant_defaults(branch_state)
    by_branch[branch_key] = branch_state
    return branch_state


async def _get_or_create_state(db: AsyncSession, tenant_id: str, business_type: str) -> TenantWorkplaceState:
    result = await db.execute(
        select(TenantWorkplaceState).where(TenantWorkplaceState.tenant_id == tenant_id)
    )
    row = result.scalar_one_or_none()
    if row:
        state = _normalize_root_state(dict(row.state_json), business_type)
        row.state_json = state
        return row
    profile = get_business_profile(business_type)
    state = dict(DEFAULT_STATE)
    branch_state = _branch_default_state()
    if profile.get("features", {}).get("appointments"):
        branch_state["appointments"] = []
        state["appointments"] = []
    if not profile.get("features", {}).get("table_management"):
        branch_state["tables"] = []
        branch_state["orders"] = []
        branch_state["kots"] = []
        branch_state["reservations"] = []
        branch_state["staff_performance"] = []
    state["by_branch"] = {DEFAULT_BRANCH_KEY: branch_state}
    row = TenantWorkplaceState(tenant_id=tenant_id, state_json=state)
    db.add(row)
    await db.flush()
    return row


async def _tenant_branches(db: AsyncSession, tenant_id: str) -> list[Branch]:
    r = await db.execute(select(Branch).where(Branch.tenant_id == tenant_id).order_by(Branch.name))
    return list(r.scalars().all())


@router.get("/state")
async def get_workplace_state(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    branch_id: str | None = Query(None, description="Branch id for per-branch workplace state"),
):
    tenant_id = require_tenant(user)
    biz = user.tenant.business_type.value if user.tenant else "retail"
    branches = await _tenant_branches(db, tenant_id)
    branch_key = _resolve_branch_key(branch_id, branches)
    row = await _get_or_create_state(db, tenant_id, biz)
    state = _normalize_root_state(dict(row.state_json), biz)
    branch_state = _get_branch_slice(state, branch_key, biz)
    row.state_json = state
    profile = get_business_profile(biz)
    appointments = state.get("appointments") or branch_state.get("appointments") or []
    return {
        "business_type": biz,
        "branch_id": branch_key,
        "workplace": profile,
        **branch_state,
        "appointments": appointments,
    }


@router.put("/state")
async def update_workplace_state(
    body: WorkplaceStateUpdate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    branch_id: str | None = Query(None, description="Branch id for per-branch workplace state"),
):
    tenant_id = require_tenant(user)
    biz = user.tenant.business_type.value if user.tenant else "retail"
    branches = await _tenant_branches(db, tenant_id)
    branch_key = _resolve_branch_key(branch_id, branches)
    row = await _get_or_create_state(db, tenant_id, biz)
    state = _normalize_root_state(dict(row.state_json), biz)
    branch_state = _get_branch_slice(state, branch_key, biz)
    if body.tables is not None:
        branch_state["tables"] = body.tables
    if body.orders is not None:
        branch_state["orders"] = body.orders
    if body.kots is not None:
        branch_state["kots"] = body.kots
    if body.reservations is not None:
        branch_state["reservations"] = body.reservations
    if body.staff_performance is not None:
        branch_state["staff_performance"] = body.staff_performance
    if body.appointments is not None:
        branch_state["appointments"] = body.appointments
        state["appointments"] = body.appointments
    state["by_branch"][branch_key] = branch_state
    row.state_json = state
    await db.flush()
    return {"business_type": biz, "branch_id": branch_key, **branch_state, "appointments": state.get("appointments", [])}
