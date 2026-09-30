"""Real authenticated directory calls against disposable databases."""
import base64
import io
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.customer_orders import generated_order_name
from app.db.directory_models import DirectoryChangeEvent, DirectoryCommand
from app.db.models import PBRMaterial, Project, PublishedBrand
from app.db.notion_sync_models import NotionSyncState, OrderFolderOperation
from test_application_access import access_case  # noqa: F401


def write(client, method, path, payload, key=None):
    return getattr(client, method)(path, json=payload, headers={"Idempotency-Key": key or str(uuid4())})


def create_customer(client, **overrides):
    response = write(client, "post", "/api/customers", {"name": "English-Dekor", "brand_identifier": "E001", **overrides})
    assert response.status_code == 201, response.text
    return response.json()


def create_order(client, customer, **overrides):
    response = write(client, "post", "/api/orders", {"customer_id": customer["id"], "project_type": "VISUALIZATIONS+SCANNING_FABRICS", "starting_date": "2026-03-15", **overrides})
    assert response.status_code == 201, response.text
    return response.json()


def test_formula_keeps_notion_join_semantics():
    assert generated_order_name("0246", "English-Dekor", "VISUALIZATIONS+SCANNING_FABRICS", date(2026, 3, 15)) == "0246_ENGLISH-DEKOR_VISUALIZATIONS+SCANNING_FABRICS_032026"
    assert generated_order_name("0247", "Name with spaces", "scanning_hpl", date(2026, 9, 1)) == "0247_NAME WITH SPACES_SCANNING_HPL_092026"


def test_customer_create_is_one_level_keyed_and_preserves_nullable_public_identifier(access_case):
    with access_case.client("ADMIN") as client:
        key = str(uuid4())
        payload = {"name": "No identifier yet", "website": "https://example.invalid", "status": "In library"}
        first = write(client, "post", "/api/customers", payload, key)
        assert first.status_code == 201, first.text
        customer = first.json()
        assert customer["brand_identifier"] is None
        assert customer["sync"]["status"] == "PENDING" and customer["sync"]["enabled"] is False
        replay = write(client, "post", "/api/customers", payload, key)
        assert replay.json() == customer and replay.headers["Idempotency-Replayed"] == "true"
        assert client.get("/api/directory-commands/" + key).json()["response"] == customer
        assert write(client, "post", "/api/customers", {**payload, "name": "Other"}, key).status_code == 409
    with access_case.database.session() as session:
        brand = session.get(PublishedBrand, UUID(customer["id"]))
        assert brand.brand_identifier.startswith("unassigned-")
        assert brand.customer_brand_identifier is None
        assert len(list(session.scalars(select(PublishedBrand).where(PublishedBrand.company_id == brand.company_id)))) == 1
        state = session.scalar(select(NotionSyncState).where(NotionSyncState.entity_id == brand.id))
        assert state.payload["brand_identifier"] is None


def test_customer_inline_edit_filters_and_material_categories_include_archived(access_case):
    from test_material_archives import apply, prepare
    with access_case.client("ADMIN") as client:
        assert apply(client, access_case.materials[0].id, prepare(client, access_case.materials[0].id)).status_code == 200
    with access_case.database.session() as session:
        material = session.get(PBRMaterial, access_case.materials[0].id)
        assert material.is_archived
        material.main_category_code = "ARCHIVE-CATEGORY"
        session.commit()
    with access_case.client("ADMIN") as client:
        rows = client.get("/api/customers", params={"main_category_code": "ARCHIVE-CATEGORY"}).json()
        assert len(rows) == 1 and rows[0]["main_category_codes"] == ["ARCHIVE-CATEGORY", "G03"]
        row = rows[0]
        payload = {"expected_updated_at": row["updated_at"], "notes": "#priority-client", "shipping_address": "Prague", "status": "test"}
        key = str(uuid4())
        saved = write(client, "patch", "/api/customers/" + row["id"], payload, key)
        assert saved.status_code == 200, saved.text
        assert saved.json()["updated_at"] != row["updated_at"]
        assert write(client, "patch", "/api/customers/" + row["id"], payload).status_code == 409
        assert write(client, "patch", "/api/customers/" + row["id"], payload, key).json() == saved.json()
        assert client.get("/api/customers", params={"search": "#priority-client", "status": "test"}).json()[0]["id"] == row["id"]
        history = client.get("/api/customers/" + row["id"] + "/history").json()["items"]
        assert history[0]["actor_name"] == "ADMIN" and history[0]["after"]["notes"] == "#priority-client"


def test_orders_preserve_legacy_material_keys_and_enqueue_only_after_local_create(access_case):
    with access_case.client("ADMIN") as client:
        customer = create_customer(client)
        order = create_order(client, customer, number="0246", priority="Urgent", status="Samples Obtained")
        assert order["generated_name"] == "0246_ENGLISH-DEKOR_VISUALIZATIONS+SCANNING_FABRICS_032026"
        assert order["folder_path"] is None and order["folder_operation"]["status"] == "PENDING"
        assert order["customer_id"] == customer["id"] and order["status"] == "Samples Obtained"
        assert client.get("/api/projects/" + order["id"]).json()["status"] == "IN_PROGRESS"
        assert client.get("/api/orders", params={"customer_id": customer["id"], "status": "Samples Obtained", "priority": "Urgent", "starting_from": "2026-03-01"}).json()[0]["id"] == order["id"]
        changed = write(client, "patch", "/api/orders/" + order["id"], {"expected_updated_at": order["updated_at"], "status": "Done", "notes": "#delivery"})
        assert changed.status_code == 200, changed.text
        assert changed.json()["status"] == "Done"
        assert client.get("/api/projects/" + order["id"]).json()["status"] == "DONE"
        assert create_order(client, customer)["number"] == "0247"
    with access_case.database.session() as session:
        stored = session.get(Project, UUID(order["id"]))
        assert stored.company_id == UUID(customer["legacy_company_id"])
        assert session.scalar(select(OrderFolderOperation).where(OrderFolderOperation.order_id == stored.id)).target_name == order["generated_name"]
        assert all(session.get(PBRMaterial, row.id).project_id == row.project_id for row in access_case.materials)


def test_unrelated_order_edit_preserves_multiple_notion_responsible_pages(access_case):
    with access_case.client("ADMIN") as client:
        customer = create_customer(client)
        order = create_order(client, customer)
        people = [str(uuid4()), str(uuid4())]
        with access_case.database.session() as session:
            stored = session.get(Project, UUID(order["id"]))
            stored.responsible_notion_page_ids = people
            session.commit()
        latest = client.get("/api/orders/" + order["id"]).json()
        response = write(client, "patch", "/api/orders/" + order["id"], {"expected_updated_at": latest["updated_at"], "priority": "High"})
        assert response.status_code == 200, response.text
        assert response.json()["responsible_notion_page_ids"] == people
        with access_case.database.session() as session:
            state = session.scalar(select(NotionSyncState).where(NotionSyncState.entity_id == UUID(order["id"])))
            assert state.payload["responsible_page_ids"] == people


def test_logo_is_validated_authenticated_and_returns_image_bytes(access_case):
    from PIL import Image
    output = io.BytesIO()
    Image.new("RGB", (16, 16), "#1F2444").save(output, format="PNG")
    with access_case.client("ADMIN") as client:
        customer = create_customer(client)
        payload = {"expected_updated_at": customer["updated_at"], "filename": "logo.png", "content_base64": base64.b64encode(output.getvalue()).decode()}
        response = write(client, "put", f"/api/customers/{customer['id']}/logo", payload)
        assert response.status_code == 200, response.text
        assert response.json()["has_logo"]
        image = client.get(response.json()["logo_url"])
        assert image.content == output.getvalue() and image.headers["content-type"] == "image/png"
        invalid = {**payload, "expected_updated_at": response.json()["updated_at"], "content_base64": base64.b64encode(b"<svg/>").decode()}
        assert write(client, "put", f"/api/customers/{customer['id']}/logo", invalid).status_code == 422
    with access_case.client() as client:
        assert client.get(response.json()["logo_url"]).status_code == 401


@pytest.mark.parametrize("resource", ["customers", "orders"])
def test_directory_write_permissions_and_required_revision(access_case, resource):
    with access_case.client("PROCESSOR") as client:
        assert client.get("/api/" + resource).status_code == 200
        assert client.patch(f"/api/{resource}/{uuid4()}", json={"expected_updated_at": datetime.now(UTC).isoformat()}).status_code == 403
    with access_case.client("ADMIN") as client:
        assert client.patch(f"/api/{resource}/{uuid4()}", json={}).status_code == 422


def test_customer_rename_cannot_bypass_linked_material_guard(access_case):
    with access_case.database.session() as session:
        material = session.get(PBRMaterial, access_case.materials[0].id)
        material.folder_path = "SAFE/SAFE_0001_G03"
        session.commit()
    with access_case.client("ADMIN") as client:
        row = client.get("/api/customers").json()[0]
        response = write(client, "patch", "/api/customers/" + row["id"], {"name": "Renamed", "expected_updated_at": row["updated_at"]})
        assert response.status_code == 409 and response.json()["detail"]["code"] == "CUSTOMER_RENAME_CONFIRMATION_REQUIRED"


def test_hidden_mother_brand_is_not_offered_in_material_choice(access_case):
    with access_case.database.session() as session:
        row = session.scalar(select(PublishedBrand))
        row.is_customer = False
        identifier = row.id
        session.commit()
    with access_case.client("ADMIN") as client:
        assert not client.get("/api/customers").json()
        assert not client.get("/api/brands").json()
        assert client.get("/api/brands/" + str(identifier)).status_code == 200


def test_missing_customer_identifier_blocks_publication_placeholder(access_case):
    from app.publication_content import content_review
    with access_case.database.session() as session:
        material = session.get(PBRMaterial, access_case.materials[0].id)
        brand = session.get(PublishedBrand, material.published_brand_id)
        brand.customer_brand_identifier = None
        brand.brand_identifier = "unassigned-" + uuid4().hex
        session.flush()
        assert "CONTENT_BRAND_IDENTIFIER_REQUIRED" in content_review(session, material)["errors"]


def test_customer_identifier_collision_is_rejected_without_profile_or_outbox_change(access_case):
    with access_case.client("ADMIN") as client:
        first = create_customer(client)
        second = create_customer(client, name="Another customer", brand_identifier="A001")
        response = write(client, "patch", "/api/customers/" + second["id"], {"expected_updated_at": second["updated_at"], "brand_identifier": first["brand_identifier"], "notes": "Should roll back"})
        assert response.status_code == 409
        assert client.get("/api/customers/" + second["id"]).json() == second


def test_customer_rename_refreshes_generated_order_name_but_never_moves_folder(access_case):
    with access_case.client("ADMIN") as client:
        customer = create_customer(client)
        order = create_order(client, customer)
        with access_case.database.session() as session:
            stored = session.get(Project, UUID(order["id"]))
            stored.folder_path = "R:\\0. PROJECTS\\" + stored.name
            original_path = stored.folder_path
            session.commit()
        from test_customer_rename import prepare
        path, body = prepare(client, customer["id"], name="Renamed customer")
        response = write(client, "post", path + "/rename", body)
        assert response.status_code == 200, response.text
        current = client.get("/api/orders/" + order["id"]).json()
        assert "RENAMED CUSTOMER" in current["generated_name"]
        assert current["folder_path"] == original_path and not current["folder_name_matches"]
        with access_case.database.session() as session:
            assert session.get(Project, UUID(order["id"])).name == current["generated_name"]


def test_unsafe_generated_folder_name_is_rejected_before_queuing_work(access_case):
    with access_case.client("ADMIN") as client:
        customer = create_customer(client)
        response = write(client, "post", "/api/orders", {"customer_id": customer["id"], "project_type": "../OTHER", "starting_date": "2026-09-30"})
        assert response.status_code == 422
    with access_case.database.session() as session:
        assert not list(session.scalars(select(OrderFolderOperation)))
