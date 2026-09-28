"""Authenticated, explicitly installed desktop capabilities; no arbitrary paths."""
from datetime import datetime
from uuid import UUID
from fastapi import APIRouter, HTTPException, Request
from app.api.material_review import _material
from app.auth.access import AccessDependency, CATALOG_MANAGERS, MATERIAL_EDITORS
from app.local_filesystem import LocalFilesError
from app.material_identity import require_material_idle
from app.metadata_client import MetadataClientError
from app.preview_client import PreviewClientError
from app.discovery_client import DiscoveryClientError
from app.automatic_file_check import BulkFileCheck, CheckSelection, _context as check_context, check_materials, combined_report


def build_local_files_router(database, library=None):
    router=APIRouter(prefix="/api/materials",tags=["local material files"])

    def context(session, material_id, access, *, manage=False, lock=False):
        if manage: access.check(session,CATALOG_MANAGERS)
        else: access.check(session)
        material=_material(session,material_id,access,lock=lock,historical=access.user.role=="ADMIN")
        require_material_idle(session,material_id)
        if not material.folder_path: raise HTTPException(409,{"code":"FOLDER_REQUIRED"})
        access.require_folder(material.folder_path,material.technical_identity)
        return material.folder_path,material.technical_identity,material.updated_at

    def local(request):
        if library is None: raise HTTPException(503,{"code":"LOCAL_DESKTOP_UNAVAILABLE"})
        if request.client is None or request.client.host not in {"127.0.0.1","::1","testclient"}:
            raise HTTPException(403,{"code":"LOCAL_DESKTOP_ONLY"})

    def call(material_id,access,request,operation,*,manage=False,desktop=False):
        local(request)
        with database.session() as session:
            expected=context(session,material_id,access,manage=manage,lock=desktop)
            # Desktop actions are quick and retain authorization until launched.
            if desktop:
                try: return operation(expected[0])
                except (OSError,ValueError,LocalFilesError): raise HTTPException(409,{"code":"LOCAL_FILE_ACTION_FAILED"}) from None
        result=None; failure=None
        try: result=operation(expected[0])
        except (OSError,ValueError,LocalFilesError,MetadataClientError,PreviewClientError,DiscoveryClientError): failure=True
        with database.session() as session:
            if context(session,material_id,access,manage=manage)!=expected: raise HTTPException(409,{"code":"LOCAL_MATERIAL_CHANGED"})
            if failure: raise HTTPException(409,{"code":"LOCAL_FILE_ACTION_FAILED"})
        return result

    @router.get("/{material_id}/local-files")
    def info(material_id:UUID,access:AccessDependency,request:Request):
        with database.session() as session:
            access.check(session)
            material=_material(session,material_id,access,historical=access.user.role=="ADMIN")
            linked=bool(material.folder_path)
        if library is None or not linked:
            return {"absolute_path":None,"can_open":False,"can_move":False}
        return call(material_id,access,request,lambda folder:{"absolute_path":library.absolute(folder),
            "can_open":True,"can_move":access.user.role in CATALOG_MANAGERS})

    @router.post("/{material_id}/local-files/open-folder")
    def open_folder(material_id:UUID,access:AccessDependency,request:Request):
        call(material_id,access,request,library.open_folder if library else None,desktop=True)
        return {"opened":True}

    @router.post("/{material_id}/local-files/open-metadata")
    def open_metadata(material_id:UUID,access:AccessDependency,request:Request):
        call(material_id,access,request,library.open_metadata if library else None,desktop=True)
        return {"opened":True}

    @router.post("/{material_id}/local-files/select-destination")
    def destination(material_id:UUID,access:AccessDependency,request:Request):
        return call(material_id,access,request,lambda _:library.select_destination(),manage=True)

    @router.post("/{material_id}/check-data")
    def check(material_id:UUID,access:AccessDependency,request:Request):
        local(request)
        return check_materials(database, library, access, [CheckSelection(id=material_id)])[0]

    @router.post("/check-data")
    def check_many(payload:BulkFileCheck,access:AccessDependency,request:Request):
        local(request)
        items = check_materials(database, library, access, payload.materials)
        report = combined_report(items)
        saved = {"report_path": None, "report_opened": False}
        # Derived results may advance updated_at. Reauthorize current records and
        # retain those locks while the small output file is saved/opened.
        with database.session() as session:
            access.check(session, MATERIAL_EDITORS)
            for item in sorted(items, key=lambda value: value["material_id"]):
                selection = CheckSelection(id=item["material_id"], expected_updated_at=datetime.fromisoformat(item["updated_at"]))
                check_context(session, selection, access, lock=True)
            saver = getattr(library, "save_check_report", None)
            if saver is not None:
                try:
                    saved = saver(report, open_report=payload.open_report)
                except (OSError, ValueError, LocalFilesError):
                    # Completed inspection is still readable/downloadable when
                    # a desktop editor is unavailable or reports storage is full.
                    saved = {"report_path": None, "report_opened": False}
        return {"items": items, "report": report, **saved}

    return router
