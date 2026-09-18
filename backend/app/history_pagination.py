"""Stable cursor windows for existing authorized, append-only history routes."""
from typing import Annotated

from fastapi import HTTPException, Query
from sqlalchemy import and_, or_, select

HistoryLimit = Annotated[int, Query(ge=1, le=100)]


def history_window(session, model, *, conditions=(), after=None, limit=100, order="created_at"):
    # Callers establish current role/material visibility before looking up a cursor.
    # Keep the old response shape/default bound; clients continue from the last ID.
    query = select(model).where(*conditions)
    column = getattr(model, order)
    if after is not None:
        cursor = session.scalar(query.where(model.id == after))
        if cursor is None:
            raise HTTPException(409, {"code": "HISTORY_CURSOR_INVALID"})
        value = getattr(cursor, order)
        query = query.where(or_(column < value, and_(column == value, model.id < cursor.id)))
    return list(session.scalars(query.order_by(column.desc(), model.id.desc()).limit(limit)))
