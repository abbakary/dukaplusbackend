"""Branch isolation — branch staff see one branch; owner can filter by branch when switching."""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import Select, select

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


def resolve_sale_branch_id(user: User, requested_branch_id: str | None = None) -> str | None:
    """Branch to attach on new sales — branch staff always use their branch."""
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
    return None


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


def apply_product_branch_filter(q: Select, user: User, branch_id: str | None = None) -> Select:
    effective = resolve_branch_filter(user, branch_id)
    if effective:
        return q.where(Product.branch_id == effective)
    return q


def apply_sale_branch_filter(q: Select, user: User, branch_id: str | None = None) -> Select:
    effective = resolve_branch_filter(user, branch_id)
    if effective:
        return q.where(Sale.branch_id == effective)
    return q


def apply_customer_branch_filter(q: Select, user: User, branch_id: str | None = None) -> Select:
    effective = resolve_branch_filter(user, branch_id)
    if effective:
        return q.where(Customer.branch_id == effective)
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
