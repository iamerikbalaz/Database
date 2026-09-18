"""Database ownership of source mutations. Mutators hold the material row lock."""
from fastapi import HTTPException
from sqlalchemy import or_, select, text

from app.db.models import MaterialFileOperation, MaterialPackagingExecution, MaterialPackagingState, PACKAGING_ACTIVE_STATUSES
from app.material_review import material_context

ACTIVE_STATUSES = ("RUNNING", "RECOVERY_REQUIRED")


def lock_folder_catalog(session):
    # Serialize linking a previously unknown nested path with claiming a source
    # tree. Acquire after the material row, before any brand rows.
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(737824903)"))


def require_folder_idle(session, folder):
    for operation in session.scalars(select(MaterialFileOperation).where(MaterialFileOperation.status.in_(ACTIVE_STATUSES))):
        for context in (operation.source_context, operation.target_context):
            a, b = folder.casefold(), context["folder_path"].casefold()
            if a == b or a.startswith(b + "/") or b.startswith(a + "/"):
                raise HTTPException(409, {"code": "MATERIAL_OPERATION_ACTIVE", "message": "This source tree belongs to an active identity operation."})
    paths = session.scalars(select(MaterialPackagingExecution.folder_path).join(MaterialPackagingState,
        MaterialPackagingState.execution_id == MaterialPackagingExecution.id).where(MaterialPackagingState.status.in_(PACKAGING_ACTIVE_STATUSES)))
    for path in paths:
        a, b = folder.casefold(), path.casefold()
        if a == b or a.startswith(b + "/") or b.startswith(a + "/"):
            raise HTTPException(409, {"code": "MATERIAL_OPERATION_ACTIVE", "message": "This source tree belongs to an active packaging execution."})


def identity_context(material):
    return {**material_context(material), "sequence_number": material.sequence_number}


def require_material_idle(session, material_id):
    if session.scalar(select(MaterialFileOperation.id).where(
            MaterialFileOperation.material_id == material_id,
            MaterialFileOperation.status.in_(ACTIVE_STATUSES)).limit(1)):
        raise HTTPException(409, {"code": "MATERIAL_OPERATION_ACTIVE",
                                  "message": "Reconcile the active identity operation before changing this material."})
    if session.scalar(select(MaterialPackagingState.execution_id).where(MaterialPackagingState.material_id == material_id,
            MaterialPackagingState.status.in_(PACKAGING_ACTIVE_STATUSES)).limit(1)):
        raise HTTPException(409, {"code": "MATERIAL_OPERATION_ACTIVE",
                                  "message": "Reconcile the active packaging execution before changing this material."})


def require_brand_idle(session, brand_id):
    if session.scalar(select(MaterialFileOperation.id).where(
            or_(MaterialFileOperation.source_brand_id == brand_id, MaterialFileOperation.target_brand_id == brand_id),
            MaterialFileOperation.status.in_(ACTIVE_STATUSES)).limit(1)):
        raise HTTPException(409, {"code": "BRAND_OPERATION_ACTIVE",
                                  "message": "Reconcile active identity operations before editing this brand."})
    if session.scalar(select(MaterialPackagingState.execution_id).join(MaterialPackagingExecution,
            MaterialPackagingState.execution_id == MaterialPackagingExecution.id).where(MaterialPackagingExecution.brand_id == brand_id,
            MaterialPackagingState.status.in_(PACKAGING_ACTIVE_STATUSES)).limit(1)):
        raise HTTPException(409, {"code": "BRAND_OPERATION_ACTIVE",
                                  "message": "Reconcile active packaging executions before editing this brand."})
