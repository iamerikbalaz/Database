from copy import deepcopy
from uuid import UUID, uuid4
import pytest
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from app.db.models import PBRMaterial, MaterialAuditEvent, Project, PublishedBrand, InternalUser
from app.db.material_deletion_models import MaterialDeletionOperation, MaterialDeletionOwner
from app.local_filesystem import LocalFilesError
from app.main import create_app
from test_application_access import access_case

PATH="/api/material-deletions"


class Files:
    def __init__(self): self.calls=[]; self.fail=False; self.reject=False; self.changed=0; self.callback=None
    def snapshot(self,folder): return {"directory_identity":[1,2,3,True],"entries":[{"path":"PREVIEW/SPHERE_1.png","kind":"file","size":20,"revision":self.changed}]}
    def execute(self,identifier,item):
        self.calls.append((identifier,item))
        if self.callback: self.callback()
        if self.fail: raise LocalFilesError("TEST_INTERRUPTION")
        return {"status":"REJECTED" if self.reject else "COMPLETED","error_code":"MATERIAL_DELETE_SOURCE_CHANGED" if self.reject else None}


@pytest.fixture
def case(access_case,monkeypatch):
    files=Files()
    monkeypatch.setattr("app.api.material_deletions.LocalMaterialDeletion",lambda library:files)
    settings=access_case.app.state.settings.model_copy(update={"source_mutations_enabled":True})
    access_case.app=create_app(settings,access_case.database,access_case.worker,local_library=object())
    with access_case.database.session() as session:
        for material in access_case.materials:
            row=session.get(PBRMaterial,material.id); row.folder_path="SAFE/"+row.technical_identity
        session.commit()
    return access_case,files


def review(client,materials,mode="RECORD_ONLY"):
    body={"materials":[{"id":str(item.id),"expected_updated_at":client.get("/api/materials/"+str(item.id),params={"include_archived":True}).json()["updated_at"]} for item in materials],"mode":mode}
    plan=client.post(PATH+"/plan",json=body)
    assert plan.status_code==200,plan.text
    return {**body,"idempotency_key":str(uuid4()),"confirmed":True,"expected_proposal_hash":plan.json()["proposal_hash"]},plan.json()


def test_record_only_delete_disappears_from_all_views_retains_history_and_replays(case):
    case,files=case; material=case.materials[0]
    with case.client("ADMIN") as client:
        body,plan=review(client,[material]); assert plan["can_apply"]
        result=client.post(PATH,json=body); assert result.status_code==200,result.text
        assert result.json()["status"]=="COMPLETED" and result.json()["deleted_count"]==1
        assert client.post(PATH,json=body).json()==result.json()
        assert client.get(PATH+"/"+result.json()["id"]).json()==result.json()
        assert client.get(PATH).json()=={"items":[]}
        assert str(material.id) not in {item["id"] for item in client.get("/api/materials").json()}
        assert str(material.id) not in {item["id"] for item in client.get("/api/materials?is_archived=true").json()}
        for suffix in ("", "/previews", "/review", "/content", "/history", "/metadata"):
            assert client.get("/api/materials/"+str(material.id)+suffix).status_code==404,suffix
        assert client.get("/api/material-archives/"+str(material.id)).status_code==404
        assert client.post(PATH,json={**body,"mode":"RECORD_AND_FILES"}).status_code==409
    assert not files.calls
    with case.database.session() as session:
        assert session.get(PBRMaterial,material.id) is None
        assert session.scalar(select(func.count()).select_from(PBRMaterial))==1
        order=session.scalar(select(Project).options(selectinload(Project.materials)).where(Project.id==material.project_id))
        assert material.id not in {row.id for row in order.materials}
        brand=session.get(PublishedBrand,material.published_brand_id)
        assert material.id not in {row.id for row in brand.materials}
        events=session.scalars(select(MaterialAuditEvent).where(MaterialAuditEvent.material_id==material.id,MaterialAuditEvent.event_type=="MATERIAL_DELETED")).all()
        assert len(events)==1 and events[0].actor_id==case.users["ADMIN"].id
        assert events[0].result["audit"]["record"]["folder_path"]=="SAFE/"+material.technical_identity
        tombstone=session.scalar(select(PBRMaterial).where(PBRMaterial.id==material.id).execution_options(include_deleted_materials=True))
        assert tombstone.deleted_at is not None
        tombstone.deleted_at=None
        with pytest.raises(ValueError,match="immutable"): session.flush()


@pytest.mark.parametrize("role,code",[(None,401),("PROCESSOR",403),("OTHER",403),("PRODUCTION_LEAD",403),("LEADERSHIP",403)])
def test_only_admin_can_review_apply_read_or_resume(case,role,code):
    case,files=case
    with case.client("ADMIN") as client: body,plan=review(client,case.materials[:1])
    with case.client(role) as client:
        assert client.post(PATH+"/plan",json={key:value for key,value in body.items() if key in {"materials","mode"}}).status_code==code
        assert client.post(PATH,json=body).status_code==code
        assert client.get(PATH).status_code==code
        assert client.post(PATH+"/"+str(uuid4())+"/resume",json={"confirmed":True}).status_code==code
    assert not files.calls


def test_stale_selection_changed_files_and_missing_confirmation_do_not_mutate(case):
    case,files=case
    with case.client("ADMIN") as client:
        body,plan=review(client,case.materials[:1],"RECORD_AND_FILES")
        assert client.post(PATH,json={**body,"confirmed":False}).status_code==422
        files.changed+=1
        assert client.post(PATH,json=body).json()["detail"]["code"]=="MATERIAL_DELETE_PLAN_CHANGED"
        with case.database.session() as session:
            session.get(PBRMaterial,case.materials[0].id).note="changed"; session.commit()
        assert client.post(PATH,json=body).status_code==409
        client.headers.pop("X-CSRF-Token")
        assert client.post(PATH,json=body).status_code==403
    assert not files.calls


def test_pending_deletion_holds_ownership_and_resumes_exactly_once(case):
    case,files=case; files.fail=True
    with case.client("ADMIN") as client:
        body,_=review(client,case.materials[:1],"RECORD_AND_FILES")
        result=client.post(PATH,json=body); assert result.status_code==200,result.text
        receipt=result.json(); assert receipt["status"]=="RECOVERY_REQUIRED"
        assert client.get(PATH,params={"material_ids":str(case.materials[0].id)}).json()["items"]==[receipt]
        assert client.post(PATH+"/plan",json={key:value for key,value in body.items() if key in {"materials","mode"}}).status_code==409
        assert client.patch("/api/materials/"+str(case.materials[0].id)+"/table",headers={"Idempotency-Key":str(uuid4())},json={"note":"blocked","expected_updated_at":body["materials"][0]["expected_updated_at"]}).status_code==409
        files.fail=False
        resumed=client.post(PATH+"/"+receipt["id"]+"/resume",json={"confirmed":True})
        assert resumed.status_code==200 and resumed.json()["status"]=="COMPLETED",resumed.text
        assert client.post(PATH,json=body).json()==resumed.json()
    assert len(files.calls)==2
    with case.database.session() as session: assert session.scalar(select(func.count()).select_from(MaterialDeletionOwner))==0


@pytest.mark.parametrize("folder",[None,"SAFE","SAFE/SOME_OTHER_FOLDER"])
def test_file_deletion_rejects_missing_or_nonmaterial_folders(case,folder):
    case,files=case
    with case.database.session() as session:
        session.get(PBRMaterial,case.materials[0].id).folder_path=folder; session.commit()
    with case.client("ADMIN") as client:
        body,plan=review(client,case.materials[:1],"RECORD_AND_FILES")
        assert not plan["can_apply"] and plan["items"][0]["issues"]
        assert client.post(PATH,json=body).status_code==409
        body,plan=review(client,case.materials[:1],"RECORD_ONLY"); assert plan["can_apply"]
        assert client.post(PATH,json=body).json()["status"]=="COMPLETED"
    assert not files.calls


def test_nested_record_blocks_whole_folder_removal_even_if_both_selected(case):
    case,files=case
    with case.database.session() as session:
        first,second=[session.get(PBRMaterial,item.id) for item in case.materials]
        second.folder_path="SAFE/"+first.technical_identity+"/"+second.technical_identity; session.commit()
    with case.client("ADMIN") as client:
        body,plan=review(client,case.materials,"RECORD_AND_FILES")
        assert not plan["can_apply"] and all(item["issues"][0]["code"]=="MATERIAL_DELETE_FOLDER_SHARED" for item in plan["items"])
        assert client.post(PATH,json=body).status_code==409
    assert not files.calls


def test_changed_source_rejection_keeps_record_and_releases_ownership(case):
    case,files=case; files.reject=True
    with case.client("ADMIN") as client:
        body,_=review(client,case.materials[:1],"RECORD_AND_FILES")
        result=client.post(PATH,json=body).json(); assert result["status"]=="REJECTED" and result["deleted_count"]==0
        assert client.get("/api/materials/"+str(case.materials[0].id)).status_code==200
        assert client.get(PATH).json()=={"items":[]}


def test_draft_record_without_identity_can_be_deleted_but_not_its_nonexistent_folder(case):
    case,files=case
    with case.database.session() as session:
        draft=session.get(PBRMaterial,case.materials[0].id)
        draft.is_draft=True; draft.folder_path=None; draft.technical_identity=None; draft.sequence_number=None; draft.main_category_code=None; draft.published_brand_id=None
        session.commit()
    with case.client("ADMIN") as client:
        _,plan=review(client,case.materials[:1],"RECORD_AND_FILES")
        assert not plan["can_apply"] and plan["items"][0]["issues"][0]["code"]=="MATERIAL_DELETE_FOLDER_REQUIRED"
        body,plan=review(client,case.materials[:1],"RECORD_ONLY"); assert plan["can_apply"] and plan["items"][0]["identity"] is None
        assert client.post(PATH,json=body).json()["status"]=="COMPLETED"
    assert not files.calls


def test_archived_material_deletion_cannot_be_undone_with_archive_restore(case):
    case,files=case; material=case.materials[0]; archive="/api/material-archives/"+str(material.id)
    with case.client("ADMIN") as client:
        preview=client.get(archive+"/preview",params={"action":"ARCHIVE"}).json()
        response=client.post(archive+"/commands",json={"action":"ARCHIVE","request_key":str(uuid4()),
            "expected_version":preview["version"],"expected_input_sha256":preview["input_sha256"],"acknowledge":True})
        assert response.status_code==200,response.text
        body,_=review(client,[material]); assert client.post(PATH,json=body).json()["status"]=="COMPLETED"
        assert client.get(archive+"/preview",params={"action":"RESTORE"}).status_code==404
        assert str(material.id) not in {item["material"]["id"] for item in client.get("/api/material-archives").json()["items"]}


def test_delete_updates_order_material_results_and_customer_derived_categories(case):
    case,files=case; material=case.materials[0]
    from app.customer_orders import customer_categories
    with case.client("ADMIN") as client:
        body,_=review(client,case.materials); assert client.post(PATH,json=body).json()["deleted_count"]==2
        assert client.get("/api/materials",params={"project_id":str(material.project_id)}).json()==[]
        assert client.get("/api/materials",params={"published_brand_id":str(material.published_brand_id)}).json()==[]
    with case.database.session() as session:
        assert customer_categories(session,[material.published_brand_id])[material.published_brand_id]==[]


def test_revoked_admin_after_file_io_cannot_tombstone_and_an_admin_can_recover(case):
    case,files=case
    def revoke():
        with case.database.session() as session:
            session.get(InternalUser,case.users["ADMIN"].id).role="PROCESSOR"; session.commit()
    with case.client("ADMIN") as client:
        body,_=review(client,case.materials[:1],"RECORD_AND_FILES"); files.callback=revoke
        response=client.post(PATH,json=body); assert response.status_code==403
        with case.database.session() as session:
            assert session.get(PBRMaterial,case.materials[0].id).deleted_at is None
            operation=session.scalar(select(MaterialDeletionOperation)); assert operation.status=="RUNNING"
            identifier=str(operation.id)
            session.get(InternalUser,case.users["ADMIN"].id).role="ADMIN"; session.commit()
        files.callback=None
        resumed=client.post(PATH+"/"+identifier+"/resume",json={"confirmed":True})
        assert resumed.status_code==200 and resumed.json()["status"]=="COMPLETED",resumed.text


def test_mixed_source_outcomes_are_terminal_partial_with_one_deleted_record(case):
    case,files=case
    files.callback=lambda:setattr(files,"reject",len(files.calls)==2)
    with case.client("ADMIN") as client:
        body,_=review(client,case.materials,"RECORD_AND_FILES")
        result=client.post(PATH,json=body).json()
        assert result["status"]=="PARTIAL" and result["deleted_count"]==1
        assert {item["status"] for item in result["items"]}=={"COMPLETED","REJECTED"}
        assert client.post(PATH,json=body).json()==result
        assert client.get(PATH).json()=={"items":[]}


def test_pending_folder_creation_prevents_deleting_its_record(case):
    from app.db.material_creation_models import MaterialCreationBatch
    case,files=case; material=case.materials[0]
    with case.database.session() as session:
        session.add(MaterialCreationBatch(actor_id=case.users["ADMIN"].id, customer_id=material.published_brand_id,
            request_key=uuid4(), request_hash="a"*64, request_payload={}, source_context={}, status="PARTIAL",
            items=[{"material_id":str(material.id),"status":"FAILED","folder_path":None}]))
        session.commit()
    with case.client("ADMIN") as client:
        version=client.get("/api/materials/"+str(material.id)).json()["updated_at"]
        result=client.post(PATH+"/plan",json={"materials":[{"id":str(material.id),"expected_updated_at":version}],"mode":"RECORD_ONLY"})
        assert result.status_code==409 and result.json()["detail"]["code"]=="MATERIAL_CREATION_ACTIVE",result.text
    assert not files.calls


def test_deleted_materials_still_protect_folder_overlap_and_historical_prefix(case):
    from fastapi import HTTPException
    from app.customer_orders import require_available_customer_prefix
    case,files=case; first,second=case.materials
    with case.client("ADMIN") as client:
        body,_=review(client,[second]); assert client.post(PATH,json=body).json()["status"]=="COMPLETED"
        response=client.post("/api/materials/"+str(first.id)+"/identity-plan",json={
            "target_brand_id":str(first.published_brand_id),"main_category_code":"G04",
            "target_parent":"SAFE/"+second.technical_identity})
        assert response.status_code==409 and response.json()["detail"]["code"]=="IDENTITY_FOLDER_OVERLAP",response.text
        body,_=review(client,[first]); assert client.post(PATH,json=body).json()["status"]=="COMPLETED"
    with case.database.session() as session:
        session.get(PublishedBrand,first.published_brand_id).folder_prefix="CURRENT"
        session.commit()
    with case.database.session() as session:
        with pytest.raises(HTTPException) as error:
            require_available_customer_prefix(session,"SAFE")
        assert error.value.detail["code"]=="CUSTOMER_PREFIX_RESERVED"


def test_deleted_record_only_child_folder_prevents_removing_its_live_parent(case):
    case,files=case; first,second=case.materials
    with case.database.session() as session:
        parent=session.get(PBRMaterial,first.id)
        session.get(PBRMaterial,second.id).folder_path=parent.folder_path+"/"+second.technical_identity
        session.commit()
    with case.client("ADMIN") as client:
        body,_=review(client,[second]); assert client.post(PATH,json=body).json()["status"]=="COMPLETED"
        _,plan=review(client,[first],"RECORD_AND_FILES")
        assert not plan["can_apply"] and plan["items"][0]["issues"][0]["code"]=="MATERIAL_DELETE_FOLDER_SHARED"
    assert not files.calls


def test_old_creation_receipt_never_recreates_or_exposes_deleted_material(case):
    from types import SimpleNamespace
    from test_resource_commands import creation_payload, send
    case,files=case; key=uuid4()
    payload=creation_payload(case.database,case.materials[0],"MATERIAL")
    with case.client("ADMIN") as client:
        created=send(client,"POST","/api/materials",payload,key)
        assert created.status_code==201,created.text
        material=SimpleNamespace(id=UUID(created.json()["id"]))
        body,_=review(client,[material]); assert client.post(PATH,json=body).json()["status"]=="COMPLETED"
        assert send(client,"POST","/api/materials",payload,key).status_code==404
        assert client.get("/api/resource-commands/"+str(key)).status_code==404
        assert client.get("/api/materials/"+str(material.id)).status_code==404
    assert not files.calls


def test_import_preflight_preserves_deleted_identity_reservations(case):
    from test_import_preview import plan_payload
    case,files=case; material=case.materials[0]
    with case.client("ADMIN") as client:
        body,_=review(client,[material]); assert client.post(PATH,json=body).json()["status"]=="COMPLETED"
        response=client.post("/api/material-imports/preview",json=plan_payload(case,(material.technical_identity,)))
        assert response.status_code==200,response.text
        assert not response.json()["can_confirm"]
        assert "IMPORT_MATERIAL_EXISTS" in {item["code"] for item in response.json()["findings"]}
    assert not files.calls
