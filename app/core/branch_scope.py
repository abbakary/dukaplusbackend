"""Branch isolation — branch staff see one branch; owner can filter by branch when switching."""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import Select, or_, select

from app.models import Customer, Product, Sale, StaffRole, User, UserRole


def is_tenant_wide_access(user: User) -> bool:
    """Owner / HQ admin who can view and manage all branches."""
    if user.role in (UserRole.super_admin, UserRole.vendor_owner):
        return True
    if user.staff and user.staff.role == StaffRole.owner:
        return True
    return False


def get_staff_branch_id(user: User) -> str | None:
    """Branch id for branch-scoped staff; None for tenant-wide users."""
    # Branch managers / cashiers are always isolated to their staff branch.
    if user.role == UserRole.vendor_staff:
        if user.staff and user.staff.branch_id:
            return user.staff.branch_id
        return None
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


async def resolve_sale_branch_id(
    db,
    user: User,
    tenant_id: str,
    requested_branch_id: str | None = None,
) -> str | None:
    """Branch to attach on new sales — branch staff always use their branch."""
    from app.services.branch_service import get_tenant_default_branch_id

    staff_branch = get_staff_branch_id(user)
    if staff_branch:
        if requested_branch_id and requested_branch_id not in ("all", "") and requested_branch_id != staff_branch:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cannot create records for another branch",
            )
        return staff_branch
    if requested_branch_id and requested_branch_id not in ("all", ""):
        return requested_branch_id
    return await get_tenant_default_branch_id(db, tenant_id)


def assert_branch_record_access(user: User, record_branch_id: str | None, *, label: str = "record") -> None:
    """Reject branch-scoped staff touching another branch's row."""
    staff_branch = get_staff_branch_id(user)
    if not staff_branch:
        return
    if record_branch_id != staff_branch:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"This {label} belongs to another branch",
        )


def branch_id_filter(column, effective: str | None, hq_branch_id: str | None = None):
    """Match branch rows; legacy NULL rows belong to HQ when filtering HQ."""
    if not effective:
        return None
    if hq_branch_id and effective == hq_branch_id:
        return or_(column == effective, column.is_(None))
    return column == effective


def apply_product_branch_filter(
    q: Select,
    user: User,
    branch_id: str | None = None,
    *,
    hq_branch_id: str | None = None,
) -> Select:
    effective = resolve_branch_filter(user, branch_id)
    clause = branch_id_filter(Product.branch_id, effective, hq_branch_id)
    if clause is not None:
        return q.where(clause)
    return q


def apply_sale_branch_filter(
    q: Select,
    user: User,
    branch_id: str | None = None,
    *,
    hq_branch_id: str | None = None,
) -> Select:
    effective = resolve_branch_filter(user, branch_id)
    clause = branch_id_filter(Sale.branch_id, effective, hq_branch_id)
    if clause is not None:
        return q.where(clause)
    return q


def apply_customer_branch_filter(
    q: Select,
    user: User,
    branch_id: str | None = None,
    *,
    hq_branch_id: str | None = None,
) -> Select:
    effective = resolve_branch_filter(user, branch_id)
    clause = branch_id_filter(Customer.branch_id, effective, hq_branch_id)
    if clause is not None:
        return q.where(clause)
    return q


async def load_product_for_user(db, user: User, tenant_id: str, product_id: str) -> Product:
    result = await db.execute(
        select(Product).where(Product.id == product_id, Product.tenant_id == tenant_id)
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    assert_branch_record_access(user, product.branch_id, label="product")
    return product


async def load_sale_for_user(db, user: User, tenant_id: str, sale_id: str) -> Sale:
    result = await db.execute(select(Sale).where(Sale.id == sale_id, Sale.tenant_id == tenant_id))
    sale = result.scalar_one_or_none()
    if not sale:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sale not found")
    assert_branch_record_access(user, sale.branch_id, label="sale")
    return sale


async def load_customer_for_user(db, user: User, tenant_id: str, customer_id: str) -> Customer:
    result = await db.execute(
        select(Customer).where(Customer.id == customer_id, Customer.tenant_id == tenant_id)
    )
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")
    assert_branch_record_access(user, customer.branch_id, label="customer")
    return customer
