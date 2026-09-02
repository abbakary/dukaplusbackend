"""Branch isolation helpers — owner sees all; branch staff see their branch only."""

from sqlalchemy import or_

from app.models import Product, Sale, StaffRole, User, UserRole


def is_tenant_wide_access(user: User) -> bool:
    """Owner / HQ admin who can view and manage all branches."""
    if user.role in (UserRole.super_admin, UserRole.vendor_owner):
        return True
    if user.staff and user.staff.role == StaffRole.owner:
        return True
    return False


def get_staff_branch_id(user: User) -> str | None:
    """Branch id for branch-scoped staff; None for tenant-wide users."""
    if is_tenant_wide_access(user):
        return None
    if user.staff and user.staff.branch_id:
        return user.staff.branch_id
    return None


def resolve_branch_filter(user: User, branch_id: str | None = None) -> str | None:
    """Effective branch filter for list queries."""
    staff_branch = get_staff_branch_id(user)
    if staff_branch:
        return staff_branch
    if branch_id and branch_id not in ("all", ""):
        return branch_id
    return None


def apply_product_branch_filter(q, user, branch_id: str | None = None):
    effective = resolve_branch_filter(user, branch_id)
    if effective:
        return q.where(or_(Product.branch_id == effective, Product.branch_id.is_(None)))
    return q


def apply_sale_branch_filter(q, user, branch_id: str | None = None):
    effective = resolve_branch_filter(user, branch_id)
    if effective:
        return q.where(Sale.branch_id == effective)
    return q
