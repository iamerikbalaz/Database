"""Admin-only reviewed deletion, with retained authorization and filesystem recovery."""
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import PurePosixPath
from typing import Annotated
from uuid import UUID, uuid4, uuid5
from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from app.api.material_review import _material, _state
from app.auth.access import ADMIN, AccessDependency
from app.db.models import PBRMaterial, MaterialAuditEvent
from app.db.material_deletion_models import MaterialDeletionOperation, MaterialDeletionOwner, TERMINAL
from app.local_filesystem import LocalFilesError
from app.local_material_deletion import LocalMaterialDeletion
from app.material_deletion import MaterialDeletionPlanRequest, MaterialDeletionApplyRequest, MaterialDeletionResume
from app.material_identity import lock_folder_catalog, require_material_idle, require_folder_idle
from app.material_review import canonical_hash
from app.material_table import utc
from app.resource_history import resource_snapshot


def operation_view(operation):
    return {"id":str(operation.id), "status":operation.status, "mode":operation.mode,
        "deleted_count":sum(item["status"] == "COMPLETED" for item in operation.items),
        "items":operation.items, "quarantine_retained":operation.mode == "RECORD_AND_FILES"}


def overlaps(a, b):
    a, b = a.casefold(), b.casefold()
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def build_material_deletions_router(database, settings, library=None):
    router = APIRouter(prefix="/api/material-deletions", tags=["material deletion"])
    adapter = LocalMaterialDeletion(library) if library is not None else None

    def local(request):
        if not settings.source_mutations_enabled: raise HTTPException(403, {"code":"SOURCE_MUTATIONS_DISABLED"})
        if adapter is None: raise HTTPException(503, {"code":"LOCAL_DESKTOP_UNAVAILABLE"})
        if request.client is None or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
            raise HTTPException(403, {"code":"LOCAL_DESKTOP_ONLY"})

    def authorized(session, identifier, access, *, lock=False):
        access.check(session, ADMIN)
        query = select(MaterialDeletionOperation).where(MaterialDeletionOperation.id == identifier)
        operation = session.scalar(query.with_for_update() if lock else query)
        if operation is None: raise HTTPException(404, {"code":"MATERIAL_DELETE_OPERATION_NOT_FOUND"})
        return operation

    def prepare(session, payload, access, request):
        actor = access.check(session, ADMIN)
        if payload.mode == "RECORD_AND_FILES": local(request)
        materials = {item.id:_material(session,item.id,access,lock=True,historical=True) for item in sorted(payload.materials,key=lambda item:str(item.id))}
        lock_folder_catalog(session)
        linked = list(session.execute(select(PBRMaterial.id,PBRMaterial.folder_path).where(PBRMaterial.folder_path.is_not(None))
                                      .execution_options(include_deleted_materials=True)))
        frozen=[]; views=[]
        for selection in sorted(payload.materials,key=lambda item:str(item.id)):
            material=materials[selection.id]
            require_material_idle(session,material.id)
            if utc(material.updated_at) != utc(selection.expected_updated_at):
                raise HTTPException(409,{"code":"MATERIAL_DELETE_CHANGED", "message":"Reload the selected materials before deletion."})
            if material.folder_path: require_folder_idle(session,material.folder_path)
            errors=[]; snapshot=None
            if payload.mode == "RECORD_AND_FILES":
                if not material.folder_path:
                    errors.append({"code":"MATERIAL_DELETE_FOLDER_REQUIRED", "message":"No data folder is linked. Use record-only deletion."})
                elif not material.technical_identity or PurePosixPath(material.folder_path).name.casefold() != material.technical_identity.casefold() or len(PurePosixPath(material.folder_path).parts)<2:
                    errors.append({"code":"MATERIAL_DELETE_FOLDER_UNSAFE", "message":"The linked path must identify this exact material folder inside a customer folder."})
                elif any(identifier != material.id and overlaps(material.folder_path,path) for identifier,path in linked):
                    errors.append({"code":"MATERIAL_DELETE_FOLDER_SHARED", "message":"The folder overlaps another material record. Resolve its folder connection first."})
                else:
                    try: snapshot=adapter.snapshot(material.folder_path)
                    except (OSError,ValueError,LocalFilesError):
                        errors.append({"code":"MATERIAL_DELETE_SOURCE_UNAVAILABLE", "message":"The complete folder could not be safely inspected. Check open files and access permissions."})
            views.append({"material_id":str(material.id),"material_name":material.material_name,"identity":material.technical_identity,
                "folder_path":material.folder_path,"file_count":sum(item["kind"]=="file" for item in snapshot["entries"]) if snapshot else None,"issues":errors})
            frozen.append({"material_id":str(material.id),"material_name":material.material_name,"identity":material.technical_identity,
                "brand_id":str(material.published_brand_id) if material.published_brand_id else None,"folder_path":material.folder_path,
                "updated_at":utc(material.updated_at).isoformat(),"snapshot":snapshot,"record":resource_snapshot(material)})
        value={"mode":payload.mode,"items":frozen}
        access.check(session,ADMIN)
        view={"mode":payload.mode,"total":len(views),"items":views,"proposal_hash":canonical_hash(value),
            "can_apply":not any(item["issues"] for item in views),
            "warnings":["RECORDS_REMOVED_FROM_ALL_VIEWS","AUDIT_RETAINED","PUBLISHED_EXTERNAL_FILES_UNCHANGED",
                "DATA_RECOVERY_QUARANTINE_RETAINED" if payload.mode=="RECORD_AND_FILES" else "SOURCE_FILES_UNCHANGED"]}
        return actor,view,value,materials

    @router.post("/plan")
    def plan(payload:MaterialDeletionPlanRequest,access:AccessDependency,request:Request):
        with database.session() as session: return prepare(session,payload,access,request)[1]

    @router.get("")
    def pending(access:AccessDependency,material_ids:Annotated[list[UUID]|None,Query(max_length=100)]=None):
        with database.session() as session:
            access.check(session,ADMIN)
            query=select(MaterialDeletionOperation).where(MaterialDeletionOperation.status.in_({"RUNNING","RECOVERY_REQUIRED"}))
            if material_ids:
                query=query.join(MaterialDeletionOwner).where(MaterialDeletionOwner.material_id.in_(material_ids)).distinct()
            return {"items":[operation_view(item) for item in session.scalars(query.order_by(MaterialDeletionOperation.created_at.desc()).limit(100 if material_ids else 30))]}

    @router.get("/{identifier}")
    def get_operation(identifier:UUID,access:AccessDependency):
        with database.session() as session: return operation_view(authorized(session,identifier,access))

    def finish(identifier,access,request):
        with database.session() as session:
            operation=authorized(session,identifier,access)
            if operation.status in TERMINAL: return operation_view(operation)
            count=len(operation.items)
        for index in range(count):
            with database.session() as session:
                operation=authorized(session,identifier,access,lock=True)
                if operation.status in TERMINAL: return operation_view(operation)
                current=operation.items[index]
                if current["status"] in {"COMPLETED","REJECTED"}: continue
                frozen=operation.plan["items"][index]
                material=_material(session,UUID(frozen["material_id"]),access,lock=True,historical=True)
                owner=session.get(MaterialDeletionOwner,material.id)
                if owner is None or owner.operation_id != operation.id:
                    raise HTTPException(409,{"code":"MATERIAL_DELETE_OWNERSHIP_CHANGED"})
                require_material_idle(session,material.id,deletion_operation_id=operation.id)
                lock_folder_catalog(session)
                if material.folder_path: require_folder_idle(session,material.folder_path,deletion_operation_id=operation.id)
                if resource_snapshot(material) != frozen["record"] or utc(material.updated_at).isoformat()!=frozen["updated_at"]:
                    raise HTTPException(409,{"code":"MATERIAL_DELETE_CHANGED"})
                result={"status":"COMPLETED","error_code":None}
                if operation.mode=="RECORD_AND_FILES":
                    local(request)
                    try: result=adapter.execute(operation.id,frozen)
                    except (OSError,ValueError,LocalFilesError):
                        result={"status":"RECOVERY_REQUIRED","error_code":"MATERIAL_DELETE_IO_INTERRUPTED"}
                access.check(session,ADMIN)
                item={**current,**result}
                if result["status"] in {"COMPLETED","REJECTED"}:
                    state=_state(session,material.id,create=True)
                    session.add(MaterialAuditEvent(material_id=material.id,actor_id=access.user.id,
                        event_type="MATERIAL_DELETED" if result["status"]=="COMPLETED" else "MATERIAL_DELETE_REJECTED",
                        request_key=uuid5(operation.id,str(material.id)+":finished"),request_hash=operation.request_hash,
                        generation=state.generation,revision_hash=state.revision_hash,
                        result={"status_code":200,"body":item,"audit":{"operation_id":str(operation.id),"authorized_actor_id":str(operation.actor_id),
                            "mode":operation.mode,"record":frozen["record"],"quarantine_retained":operation.mode=="RECORD_AND_FILES"}}))
                    if result["status"]=="COMPLETED":
                        material.deleted_at=datetime.now(UTC); material.updated_at=material.deleted_at
                    session.delete(owner)
                items=[dict(value) for value in operation.items]; items[index]=item; operation.items=items
                if any(value["status"] in {"RUNNING","RECOVERY_REQUIRED"} for value in items): operation.status="RECOVERY_REQUIRED"
                elif all(value["status"]=="COMPLETED" for value in items): operation.status="COMPLETED"
                elif all(value["status"]=="REJECTED" for value in items): operation.status="REJECTED"
                else: operation.status="PARTIAL"
                session.commit()
        with database.session() as session: return operation_view(authorized(session,identifier,access))

    @router.post("")
    def apply(payload:MaterialDeletionApplyRequest,access:AccessDependency,request:Request):
        request_hash=canonical_hash(payload.model_dump(mode="json"))
        with database.session() as session:
            actor=access.check(session,ADMIN)
            if session.get_bind().dialect.name == "postgresql":
                # Serialize exact retries before checking the durable receipt.
                # This lock is scoped to actor/key, not the whole material library.
                lock_key = int.from_bytes(sha256(f"material-delete:{actor.id}:{payload.idempotency_key}".encode()).digest()[:8], signed=True)
                session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
            operation=session.scalar(select(MaterialDeletionOperation).where(MaterialDeletionOperation.actor_id==actor.id,
                MaterialDeletionOperation.request_key==payload.idempotency_key).with_for_update())
            if operation is not None:
                if operation.request_hash!=request_hash: raise HTTPException(409,{"code":"MATERIAL_DELETE_REQUEST_KEY_REUSED"})
                identifier=operation.id
            else:
                actor,preview,frozen,materials=prepare(session,payload,access,request)
                if preview["proposal_hash"]!=payload.expected_proposal_hash:
                    raise HTTPException(409,{"code":"MATERIAL_DELETE_PLAN_CHANGED","message":"The selection or source folder changed. Review a fresh deletion plan."})
                if not preview["can_apply"]: raise HTTPException(409,{"code":"MATERIAL_DELETE_PLAN_BLOCKED"})
                identifier=uuid4()
                operation=MaterialDeletionOperation(id=identifier,actor_id=actor.id,request_key=payload.idempotency_key,
                    request_hash=request_hash,proposal_hash=preview["proposal_hash"],mode=payload.mode,
                    request_payload=payload.model_dump(mode="json"),plan=frozen,status="RUNNING",
                    items=[{"material_id":item["material_id"],"material_name":item["material_name"],"identity":item["identity"],
                        "status":"RUNNING","error_code":None} for item in frozen["items"]])
                try:
                    session.add(operation); session.flush()
                    for material in materials.values():
                        session.add(MaterialDeletionOwner(material_id=material.id,operation_id=identifier,
                            brand_id=material.published_brand_id,folder_path=material.folder_path))
                    session.commit()
                except IntegrityError:
                    session.rollback(); raise HTTPException(409,{"code":"MATERIAL_DELETE_OPERATION_CONFLICT"}) from None
        return finish(identifier,access,request)

    @router.post("/{identifier}/resume")
    def resume(identifier:UUID,payload:MaterialDeletionResume,access:AccessDependency,request:Request):
        return finish(identifier,access,request)

    return router
