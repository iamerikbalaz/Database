"""Independent regressions for customer rename recovery and historical sources."""
from uuid import UUID

from sqlalchemy import func, select

from app.api.source_metadata import metadata_identity
from app.db.customer_rename_models import CustomerRenameOperation
from app.db.models import MaterialFileOperation, MaterialIdentityHistory, PBRMaterial, PublishedBrand
from app.identity_client import IdentityClientError
from app.main import create_app
from test_application_access import access_case  # noqa: F401
from test_customer_orders import write
from test_customer_rename import prepare
from test_material_identity import IdentityStub


def linked_case(case, worker):
    case.app = create_app(case.app.state.settings.model_copy(update={"source_mutations_enabled": True}),
                          case.database, case.worker, identity_client=worker)
    with case.database.session() as session:
        for item in session.scalars(select(PBRMaterial)):
            item.folder_path = "library/" + item.technical_identity
            item.is_published = True
            item.workflow_status = "DONE"
        session.commit()


def test_unknown_result_retries_original_children_and_keeps_customer_owned(access_case):
    case = access_case
    worker = IdentityStub()
    linked_case(case, worker)
    worker.failure = IdentityClientError()
    with case.client("ADMIN") as client:
        path, body = prepare(client, case.materials[0].published_brand_id, rename_materials=True)
        result = write(client, "post", path + "/rename", body)
        assert result.status_code == 200, result.text
        batch = result.json()
        assert batch["status"] == "RECOVERY_REQUIRED"
        assert batch["completed_count"] == 0
        original_ids = [row["operation_id"] for row in batch["materials"]]
        assert len(worker.executions) == 1
        edit = write(client, "patch", path, {"expected_updated_at": batch["customer"]["updated_at"], "notes": "Blocked"})
        assert edit.status_code == 409, edit.text
        assert edit.json()["detail"]["code"] == "CUSTOMER_RENAME_ACTIVE"
        for material in case.materials:
            edit = client.patch(f"/api/materials/{material.id}", json={"material_name": "Blocked"})
            assert edit.status_code == 409, edit.text
        worker.failure = None
        result = write(client, "post", path + f"/rename-operations/{batch['id']}/resume", {})
        assert result.status_code == 200 and result.json()["status"] == "COMPLETED", result.text
        assert [row["operation_id"] for row in result.json()["materials"]] == original_ids
        assert [row["operation_id"] for row in worker.executions] == [original_ids[0], *original_ids]
        assert write(client, "post", path + f"/rename-operations/{batch['id']}/resume", {}).json() == result.json()
        assert len(worker.executions) == 3
    with case.database.session() as session:
        assert session.scalar(select(func.count()).select_from(MaterialFileOperation)) == 2
        assert session.scalar(select(func.count()).select_from(MaterialIdentityHistory)) == 2
        assert session.scalar(select(func.count()).select_from(CustomerRenameOperation)) == 1
        assert not any(item.is_published for item in session.scalars(select(PBRMaterial)))


def test_terminal_partial_needs_fresh_plan_only_for_failed_material(access_case):
    class RejectFirst(IdentityStub):
        def execute(self, request):
            self.outcome = "REJECTED" if not self.executions else "COMPLETED"
            return super().execute(request)

    case = access_case
    worker = RejectFirst()
    linked_case(case, worker)
    with case.client("ADMIN") as client:
        path, body = prepare(client, case.materials[0].published_brand_id, rename_materials=True)
        initial = write(client, "post", path + "/rename", body)
        assert initial.status_code == 200, initial.text
        batch = initial.json()
        assert batch["status"] == "PARTIAL" and batch["completed_count"] == 1
        failed_id = next(row["material_id"] for row in batch["materials"] if row["status"] == "REJECTED")
        with case.database.session() as session:
            failed = session.get(PBRMaterial, UUID(failed_id))
            assert failed.technical_identity.startswith("SAFE_") and failed.is_published
            assert metadata_identity(failed)["MANUFACTURER"] == "Synthetic brand"
        replay = write(client, "post", path + f"/rename-operations/{batch['id']}/resume", {})
        assert replay.json()["status"] == "PARTIAL" and len(worker.executions) == 2
        _, next_body = prepare(client, case.materials[0].published_brand_id, rename_materials=True)
        final = write(client, "post", path + "/rename", next_body)
        assert final.status_code == 200, final.text
        assert final.json()["status"] == "COMPLETED" and final.json()["total_count"] == 1
        assert final.json()["materials"][0]["material_id"] == failed_id
    with case.database.session() as session:
        assert session.scalar(select(func.count()).select_from(MaterialIdentityHistory)) == 2
        assert session.scalar(select(func.count()).select_from(MaterialFileOperation)) == 3
        assert not any(item.is_published for item in session.scalars(select(PBRMaterial)))


def test_future_only_customer_rename_retains_source_prefix_on_material_name_edit(access_case):
    case = access_case
    worker = IdentityStub()
    case.app = create_app(case.app.state.settings.model_copy(update={"source_mutations_enabled": True}),
                          case.database, case.worker, identity_client=worker)
    with case.database.session() as session:
        item = session.get(PBRMaterial, case.materials[0].id)
        item.folder_path = "library/" + item.technical_identity
        session.commit()
    with case.client("ADMIN") as client:
        path, body = prepare(client, case.materials[0].published_brand_id)
        assert write(client, "post", path + "/rename", body).status_code == 200
        response = client.post(f"/api/materials/{case.materials[0].id}/identity-plan", json={
            "target_brand_id": str(case.materials[0].published_brand_id), "main_category_code": "G03", "material_name": "New name"})
        assert response.status_code == 200, response.text
        target = response.json()["target_context"]
        assert target["technical_identity"] == "SAFE_0001_NEW-NAME_G03"
        assert target["brand_name"] == "Synthetic brand"
        assert worker.plans[-1]["brand_name"] == "Synthetic brand"


def test_historical_material_prefix_is_reserved_after_future_only_rename(access_case):
    case = access_case
    with case.client("ADMIN") as client:
        path, body = prepare(client, case.materials[0].published_brand_id)
        assert write(client, "post", path + "/rename", body).status_code == 200
        result = write(client, "post", "/api/customers", {"name": "Another customer", "folder_prefix": "SAFE"})
        assert result.status_code == 409, result.text


def test_active_customer_rename_is_not_a_target_for_other_material_rebrand(access_case):
    case = access_case
    worker = IdentityStub()
    linked_case(case, worker)
    with case.database.session() as session:
        customer = session.get(PublishedBrand, case.materials[0].published_brand_id)
        other = PublishedBrand(company_id=customer.company_id, name="Other", folder_prefix="OTHER", brand_identifier="other")
        session.add(other)
        session.flush()
        moved = session.get(PBRMaterial, case.materials[1].id)
        moved.published_brand_id = other.id
        moved.is_published = False
        moved.workflow_status = "IN_PROGRESS"
        session.commit()
    worker.failure = IdentityClientError()
    with case.client("ADMIN") as client:
        path, body = prepare(client, case.materials[0].published_brand_id, rename_materials=True)
        result = write(client, "post", path + "/rename", body)
        assert result.status_code == 200 and result.json()["status"] == "RECOVERY_REQUIRED", result.text
        response = client.post(f"/api/materials/{case.materials[1].id}/identity-plan", json={
            "target_brand_id": str(case.materials[0].published_brand_id), "main_category_code": "G03"})
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "CUSTOMER_RENAME_ACTIVE"
