"""Database ownership of source mutations. Mutators hold the material row lock."""
from fastapi import HTTPException
from sqlalchemy import String, or_, select, text

from app.db.models import MaterialFileOperation, MaterialMetadataOperation, MaterialPackagingExecution, MaterialPackagingState, PACKAGING_ACTIVE_STATUSES
from app.db.models import PublicationStagingItem, PublicationStagingOwner
from app.material_review import material_context

ACTIVE_STATUSES = ("RUNNING", "RECOVERY_REQUIRED")


def require_customer_rename_idle(session, brand_id):
    from app.db.customer_rename_models import CustomerRenameOperation
    if session.scalar(select(CustomerRenameOperation.id).where(
            CustomerRenameOperation.customer_id == brand_id,
            CustomerRenameOperation.status.in_(ACTIVE_STATUSES)).limit(1)):
        raise HTTPException(409, {"code": "CUSTOMER_RENAME_ACTIVE", "message": "Finish or resume the active customer rename first."})


def lock_folder_catalog(session):
    # Serialize linking a previously unknown nested path with claiming a source
    # tree. Acquire after the material row, before any brand rows.
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(737824903)"))


def require_folder_idle(session, folder, *, packaging_execution_id=None, staging_job_id=None, preview_operation_id=None, deletion_operation_id=None):
    from app.db.material_deletion_models import MaterialDeletionOwner
    deletion_owners = select(MaterialDeletionOwner).where(MaterialDeletionOwner.folder_path.is_not(None))
    if deletion_operation_id is not None: deletion_owners = deletion_owners.where(MaterialDeletionOwner.operation_id != deletion_operation_id)
    for owner in session.scalars(deletion_owners):
        a, b = folder.casefold(), owner.folder_path.casefold()
        if a == b or a.startswith(b + "/") or b.startswith(a + "/"):
            raise HTTPException(409, {"code":"MATERIAL_OPERATION_ACTIVE", "message":"Finish the active material deletion first."})
    from app.db.preview_edit_models import PreviewEditOwner
    owners = select(PreviewEditOwner)
    if preview_operation_id is not None: owners = owners.where(PreviewEditOwner.operation_id != preview_operation_id)
    for owner in session.scalars(owners):
        a, b = folder.casefold(), owner.folder_path.casefold()
        if a == b or a.startswith(b + "/") or b.startswith(a + "/"):
            raise HTTPException(409, {"code": "MATERIAL_OPERATION_ACTIVE", "message": "Finish the active preview file edit first."})
    for path in session.scalars(select(MaterialMetadataOperation.folder_path).where(MaterialMetadataOperation.status == "RUNNING")):
        a, b = folder.casefold(), path.casefold()
        if a == b or a.startswith(b + "/") or b.startswith(a + "/"):
            raise HTTPException(409, {"code": "MATERIAL_OPERATION_ACTIVE", "message": "Reconcile the active source metadata edit first."})
    for operation in session.scalars(select(MaterialFileOperation).where(MaterialFileOperation.status.in_(ACTIVE_STATUSES))):
        for context in (operation.source_context, operation.target_context):
            a, b = folder.casefold(), context["folder_path"].casefold()
            if a == b or a.startswith(b + "/") or b.startswith(a + "/"):
                raise HTTPException(409, {"code": "MATERIAL_OPERATION_ACTIVE", "message": "This source tree belongs to an active identity operation."})
    query = select(MaterialPackagingExecution.folder_path).join(MaterialPackagingState,
        MaterialPackagingState.execution_id == MaterialPackagingExecution.id).where(MaterialPackagingState.status.in_(PACKAGING_ACTIVE_STATUSES))
    if packaging_execution_id is not None:
        query = query.where(MaterialPackagingExecution.id != packaging_execution_id)
    paths = session.scalars(query)
    for path in paths:
        a, b = folder.casefold(), path.casefold()
        if a == b or a.startswith(b + "/") or b.startswith(a + "/"):
            raise HTTPException(409, {"code": "MATERIAL_OPERATION_ACTIVE", "message": "This source tree belongs to an active packaging execution."})
    staging_query = select(PublicationStagingItem.folder_path).join(PublicationStagingOwner,
        (PublicationStagingOwner.job_id == PublicationStagingItem.job_id)
        & (PublicationStagingOwner.material_id == PublicationStagingItem.material_id)).where(PublicationStagingOwner.active.is_(True))
    if staging_job_id is not None:
        staging_query = staging_query.where(PublicationStagingItem.job_id != staging_job_id)
    for path in session.scalars(staging_query):
        a, b = folder.casefold(), path.casefold()
        if a == b or a.startswith(b + "/") or b.startswith(a + "/"):
            raise HTTPException(409, {"code": "MATERIAL_OPERATION_ACTIVE", "message": "This source tree belongs to an active staging reservation."})


def identity_context(material):
    return {**material_context(material), "sequence_number": material.sequence_number}


def require_material_idle(session, material_id, *, packaging_execution_id=None, staging_job_id=None, preview_operation_id=None, deletion_operation_id=None, creation_batch_id=None):
    from app.db.material_creation_models import MaterialCreationBatch
    creation = select(MaterialCreationBatch.id).where(MaterialCreationBatch.status != "COMPLETED",
        MaterialCreationBatch.items.cast(String).contains(str(material_id)))
    if creation_batch_id is not None:
        creation = creation.where(MaterialCreationBatch.id != creation_batch_id)
    if session.scalar(creation.limit(1)):
        raise HTTPException(409, {"code": "MATERIAL_CREATION_ACTIVE", "message": "Recover the pending material folder creation first."})
    from app.db.material_deletion_models import MaterialDeletionOwner
    deletion_owner = session.get(MaterialDeletionOwner, material_id)
    if deletion_owner is not None and deletion_owner.operation_id != deletion_operation_id:
        raise HTTPException(409, {"code":"MATERIAL_OPERATION_ACTIVE", "message":"Finish the active material deletion first."})
    from app.db.models import PBRMaterial
    from app.db.preview_edit_models import PreviewEditOwner
    preview_owner = session.get(PreviewEditOwner, material_id)
    if preview_owner is not None and preview_owner.operation_id != preview_operation_id:
        raise HTTPException(409, {"code": "MATERIAL_OPERATION_ACTIVE", "message": "Finish the active preview file edit first."})
    brand_id = session.scalar(select(PBRMaterial.published_brand_id).where(PBRMaterial.id == material_id))
    if brand_id is not None:
        require_customer_rename_idle(session, brand_id)
    if session.scalar(select(MaterialMetadataOperation.id).where(MaterialMetadataOperation.material_id == material_id,
            MaterialMetadataOperation.status == "RUNNING").limit(1)):
        raise HTTPException(409, {"code": "MATERIAL_OPERATION_ACTIVE", "message": "Reconcile the active source metadata edit first."})
    owner = session.scalar(select(PublicationStagingOwner.job_id).where(
        PublicationStagingOwner.material_id == material_id, PublicationStagingOwner.active.is_(True)).limit(1))
    if staging_job_id is not None and owner != staging_job_id:
        raise HTTPException(409, {"code": "GCS_STAGING_OWNERSHIP_CHANGED"})
    if owner is not None and owner != staging_job_id:
        raise HTTPException(409, {"code": "MATERIAL_OPERATION_ACTIVE", "message": "Close or complete the active staging job before changing this material."})
    if session.scalar(select(MaterialFileOperation.id).where(
            MaterialFileOperation.material_id == material_id,
            MaterialFileOperation.status.in_(ACTIVE_STATUSES)).limit(1)):
        raise HTTPException(409, {"code": "MATERIAL_OPERATION_ACTIVE",
                                  "message": "Reconcile the active identity operation before changing this material."})
    query = select(MaterialPackagingState.execution_id).where(MaterialPackagingState.material_id == material_id,
        MaterialPackagingState.status.in_(PACKAGING_ACTIVE_STATUSES))
    if packaging_execution_id is not None:
        query = query.where(MaterialPackagingState.execution_id != packaging_execution_id)
    if session.scalar(query.limit(1)):
        raise HTTPException(409, {"code": "MATERIAL_OPERATION_ACTIVE",
                                  "message": "Reconcile the active packaging execution before changing this material."})


def require_brand_idle(session, brand_id):
    from app.db.material_deletion_models import MaterialDeletionOwner
    if session.scalar(select(MaterialDeletionOwner.material_id).where(MaterialDeletionOwner.brand_id == brand_id).limit(1)):
        raise HTTPException(409, {"code":"BRAND_OPERATION_ACTIVE", "message":"Finish active material deletions first."})
    from app.db.preview_edit_models import PreviewEditOwner
    if session.scalar(select(PreviewEditOwner.material_id).where(PreviewEditOwner.brand_id == brand_id).limit(1)):
        raise HTTPException(409, {"code": "BRAND_OPERATION_ACTIVE", "message": "Finish active preview file edits first."})
    require_customer_rename_idle(session, brand_id)
    if session.scalar(select(MaterialMetadataOperation.id).where(MaterialMetadataOperation.brand_id == brand_id,
            MaterialMetadataOperation.status == "RUNNING").limit(1)):
        raise HTTPException(409, {"code": "BRAND_OPERATION_ACTIVE", "message": "Reconcile active source metadata edits first."})
    if session.scalar(select(PublicationStagingOwner.job_id).join(PublicationStagingItem,
            (PublicationStagingOwner.job_id == PublicationStagingItem.job_id)
            & (PublicationStagingOwner.material_id == PublicationStagingItem.material_id)).where(
            PublicationStagingItem.brand_id == brand_id, PublicationStagingOwner.active.is_(True)).limit(1)):
        raise HTTPException(409, {"code": "BRAND_OPERATION_ACTIVE", "message": "Close or complete active staging jobs before editing this brand."})
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
