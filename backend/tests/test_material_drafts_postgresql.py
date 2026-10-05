"""Real nullable draft invariants, upgrades, and serialized number allocation."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from uuid import UUID, uuid4
from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from app.core.config import get_settings
from app.db.models import PBRMaterial, PublishedBrand, MaterialNumberReservation
from test_materials_postgresql import POSTGRES_TEST_ADMIN_URL, _review_pg_case, isolated_postgresql_database, migrated_postgresql_url  # noqa: F401

pytestmark = pytest.mark.skipif(POSTGRES_TEST_ADMIN_URL is None, reason="isolated PostgreSQL required")


def test_0043_preserves_existing_material_facts_and_guards_nullable_drafts():
    with isolated_postgresql_database() as url, pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", url); get_settings.cache_clear()
        try:
            config = Config("alembic.ini"); command.upgrade(config, "20261001_0042")
            with contextmanager(_review_pg_case)(url) as case:
                with case.database.engine.connect() as connection:
                    before = connection.execute(text("SELECT to_jsonb(m) FROM pbr_materials m ORDER BY id")).scalars().all()
                command.upgrade(config, "head"); command.check(config)
                with case.database.engine.connect() as connection:
                    after = connection.execute(text("SELECT to_jsonb(m)-'is_draft'-'deleted_at' FROM pbr_materials m ORDER BY id")).scalars().all()
                    assert before == after
                draft_id = uuid4()
                with case.database.engine.begin() as connection:
                    connection.execute(text("INSERT INTO pbr_materials(id,material_name,is_draft) VALUES (:id,'ONLY-NAME',true)"), {"id": draft_id})
                for update in ("folder_path='invented/folder'", "technical_identity='INVENTED'", "sequence_number=1", "is_published=true", "workflow_status='DONE'", "is_draft=false"):
                    with pytest.raises(IntegrityError), case.database.engine.begin() as connection:
                        connection.execute(text("UPDATE pbr_materials SET " + update + " WHERE id=:id"), {"id": draft_id})
                for field in ("published_brand_id", "main_category_code", "technical_identity", "sequence_number"):
                    with pytest.raises(IntegrityError), case.database.engine.begin() as connection:
                        connection.execute(text("UPDATE pbr_materials SET " + field + "=NULL WHERE id=:id"), {"id": case.material.id})
                with pytest.raises(RuntimeError, match="incomplete material records"):
                    command.downgrade(config, "20261001_0042")
                with case.database.engine.connect() as connection:
                    assert connection.execute(text("SELECT is_draft FROM pbr_materials WHERE id=:id"), {"id": draft_id}).scalar_one() is True
        finally:
            get_settings.cache_clear()


@pytest.mark.parametrize("second_actor", [0, 3])
def test_concurrent_draft_completion_reserves_distinct_numbers_and_keeps_no_folder(migrated_postgresql_url, second_actor):
    with contextmanager(_review_pg_case)(migrated_postgresql_url) as case:
        brand_id = case.material.published_brand_id
        with case.database.session() as session:
            brand = session.get(PublishedBrand, brand_id); brand.next_sequence_number = 2; session.commit()
        # Different administrators exercise the brand row allocator directly;
        # same-actor requests also verify credential/session lock serialization.
        with case.client_for() as first, case.client_for(second_actor) as second:
            batch = first.post("/api/material-create-batches", json={"idempotency_key": str(uuid4()),
                "published_brand_id": str(brand_id), "names": ["First Draft", "Second Draft"]})
            assert batch.status_code == 200, batch.text
            identifiers = [item["material_id"] for item in batch.json()["items"]]
            versions = [first.get("/api/materials/" + identifier).json()["updated_at"] for identifier in identifiers]
            def finish(client, identifier, expected):
                request = {"expected_updated_at": expected, "main_category_code": "G03"}
                key = str(uuid4())
                result = client.patch("/api/materials/" + identifier + "/table", json=request, headers={"Idempotency-Key": key})
                assert result.status_code == 200, result.text
                assert client.patch("/api/materials/" + identifier + "/table", json=request, headers={"Idempotency-Key": key}).json() == result.json()
                return result.json()
            with ThreadPoolExecutor(2) as pool:
                futures = [pool.submit(finish, client, identifier, version) for client, identifier, version in zip((first, second), identifiers, versions)]
                results = [future.result(timeout=20) for future in futures]
            assert {item["sequence_number"] for item in results} == {2, 3}
            assert all(item["folder_path"] is None and item["is_draft"] is False for item in results)
        with case.database.session() as session:
            assert session.get(PublishedBrand, brand_id).next_sequence_number == 4
            reserved = session.scalars(select(MaterialNumberReservation).where(MaterialNumberReservation.material_id.in_([UUID(item) for item in identifiers]))).all()
            assert len(reserved) == 2
