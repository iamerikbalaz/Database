"""Customer consistency applies when an assignment is changed, not retroactively."""
from fastapi import HTTPException
from sqlalchemy import select

from app.db.models import Project


def require_order_customer(session, project_id, customer_id, *, allow_unassigned=False):
    if project_id is None:
        return
    order = session.scalar(select(Project).where(Project.id == project_id).with_for_update(read=True))
    if order is None:
        raise HTTPException(404, "Order not found.")
    # Older imported orders may not have an explicit Customer yet. Their
    # historical assignment remains usable until that relation is supplied.
    if order.customer_id is not None and order.customer_id != customer_id and not (allow_unassigned and customer_id is None):
        raise HTTPException(409, {"code": "MATERIAL_ORDER_CUSTOMER_MISMATCH",
            "message": "The material Customer must match the selected Order Customer."})
