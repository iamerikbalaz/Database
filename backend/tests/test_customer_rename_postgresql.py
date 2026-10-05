"""PostgreSQL rename serialization, immutable authorization and migration proof."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError

from app.core.config import get_settings
from app.db.customer_rename_models import CustomerRenameOperation
from app.db.models import PBRMaterial, PublishedBrand
from test_materials_postgresql import POSTGRES_TEST_ADMIN_URL, isolated_postgresql_database, migrated_postgresql_url, review_pg_case, _create_postgresql_material_with_metadata  # noqa: F401

pytestmark = pytest.mark.skipif(POSTGRES_TEST_ADMIN_URL is None, reason="isolated PostgreSQL required")


def test_competing_customer_rename_has_single_winner_and_immutable_authorization(review_pg_case):
    case = review_pg_case
    path = "/api/customers/" + str(case.material.published_brand_id)
    with case.client_for() as client:
        row = client.get(path).json()
        bodies = {}
        for actor in (0, 3):
            body = {"name": "Renamed " + uuid4().hex, "rename_materials": False, "expected_updated_at": row["updated_at"]}
            result = client.post(path + "/rename-plan", json=body)
            assert result.status_code == 200, result.text
            bodies[actor] = {**body, "confirmed": True, "expected_proposal_hash": result.json()["proposal_hash"]}
    barrier = Barrier(2)
    keys = {actor: str(uuid4()) for actor in bodies}
    def execute(actor):
        with case.client_for(actor) as client:
            barrier.wait(timeout=15)
            return actor, client.post(path + "/rename", json=bodies[actor], headers={"Idempotency-Key": keys[actor]})
    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(execute, (0, 3)))
    assert sorted(item.status_code for _, item in responses) == [200, 409], [item.text for _, item in responses]
    actor, response = next((actor, item) for actor, item in responses if item.status_code == 200)
    with case.client_for(actor) as client:
        assert client.post(path + "/rename", json=bodies[actor], headers={"Idempotency-Key": keys[actor]}).json() == response.json()
    with case.database.session() as session:
        operations = list(session.scalars(select(CustomerRenameOperation)))
        assert len(operations) == 1
        operation_id = operations[0].id
        material = session.get(PBRMaterial, case.material.id)
        assert material.source_brand_name == row["name"] and material.technical_identity == case.material.technical_identity
    for statement in ("DELETE FROM customer_rename_operations WHERE id=:id", "UPDATE customer_rename_operations SET authorization='{}'::jsonb WHERE id=:id", "UPDATE customer_rename_operations SET status='RUNNING' WHERE id=:id"):
        with pytest.raises(DBAPIError), case.database.engine.begin() as connection:
            connection.execute(text(statement), {"id": operation_id})


def test_material_receipt_guard_excludes_source_identity_snapshot_only(review_pg_case):
    case = review_pg_case
    with case.database.session() as session:
        brand = session.get(PublishedBrand, case.material.published_brand_id)
        brand.next_sequence_number += 1
        session.commit()
    with case.client_for() as client:
        key = str(uuid4())
        payload = {"project_id": str(case.material.project_id), "published_brand_id": str(case.material.published_brand_id),
                   "assigned_processor_id": str(case.material.assigned_processor_id), "material_name": "Receipt sample", "main_category_code": "G03"}
        result = client.post("/api/materials", json=payload, headers={"Idempotency-Key": key})
        assert result.status_code == 201, result.text
        assert client.post("/api/materials", json=payload, headers={"Idempotency-Key": key}).json() == result.json()
        assert "source_brand_name" not in result.json()
        assert client.get("/api/resource-commands/" + key).json()["response"] == result.json()


def test_0036_preserves_0035_materials_and_prevents_losing_historical_manufacturer():
    with isolated_postgresql_database() as url, pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", url)
        get_settings.cache_clear()
        engine = create_engine(url)
        try:
            config = Config("alembic.ini")
            command.upgrade(config, "20260930_0035")
            material_id = _create_postgresql_material_with_metadata(url, uuid4().hex[:12])
            with engine.connect() as connection:
                original = dict(connection.execute(text("SELECT * FROM pbr_materials WHERE id=:id"), {"id": material_id}).mappings().one())
                manufacturer = connection.scalar(text("SELECT name FROM published_brands WHERE id=:id"), {"id": original["published_brand_id"]})
            command.upgrade(config, "head")
            command.check(config)
            with engine.connect() as connection:
                updated = dict(connection.execute(text("SELECT * FROM pbr_materials WHERE id=:id"), {"id": material_id}).mappings().one())
                assert updated == {**original, "source_brand_name": manufacturer, "is_draft": False, "deleted_at": None}
            command.downgrade(config, "20260930_0035")
            command.upgrade(config, "head")
            with engine.begin() as connection:
                connection.execute(text("UPDATE published_brands SET name='New customer name' WHERE id=:id"), {"id": original["published_brand_id"]})
            with pytest.raises(RuntimeError, match="Historical source manufacturer"):
                command.downgrade(config, "20260930_0035")
            with engine.connect() as connection:
                assert connection.scalar(text("SELECT source_brand_name FROM pbr_materials WHERE id=:id"), {"id": material_id}) == manufacturer
        finally:
            engine.dispose()
            get_settings.cache_clear()
