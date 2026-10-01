"""Real PostgreSQL guards and races for catalog corrections."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.core.config import get_settings
from app.db.models import OnlineCategory, CatalogAuditEvent
from test_catalog_table import update
from test_materials_postgresql import _review_pg_case, isolated_postgresql_database, POSTGRES_TEST_ADMIN_URL

pytestmark = pytest.mark.skipif(POSTGRES_TEST_ADMIN_URL is None, reason="isolated PostgreSQL required")


def test_catalog_identity_migration_and_concurrent_abbreviation_uniqueness():
    with isolated_postgresql_database() as url, pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", url); get_settings.cache_clear()
        try:
            config = Config("alembic.ini"); command.upgrade(config, "head")
            with contextmanager(_review_pg_case)(url) as case:
                with case.client_for() as first, case.client_for(3) as second:
                    created = [first.post("/api/online-categories", json={"idempotency_key": str(uuid4()), "value": f"Synthetic catalog race {index}"}).json() for index in range(2)]
                    barrier = Barrier(2)
                    def change(index, client):
                        barrier.wait(timeout=15)
                        return client.patch(f"/api/online-categories/{created[index]['id']}/identity", json=update(value=f"Corrected synthetic category {index}", abbreviation="TEST-RACE"))
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        futures = [pool.submit(change, 0, first), pool.submit(change, 1, second)]
                        results = [future.result(timeout=30) for future in futures]
                    assert sorted(result.status_code for result in results) == [200, 409]
                    winner = next(result.json() for result in results if result.status_code == 200)
                    with case.database.engine.connect() as connection:
                        assert connection.execute(text("SELECT count(*) FROM online_categories WHERE abbreviation='TEST-RACE'")).scalar() == 1
                    renamed = first.patch(f"/api/online-categories/{winner['id']}/identity", json=update(2, value=winner["value"], abbreviation="TEST-NEW"))
                    assert renamed.status_code == 200 and set(renamed.json()["aliases"]) == {"TEST-RACE", "TEST-NEW"}
                    loser = next(item for item in created if item["id"] != winner["id"])
                    assert first.patch(f"/api/online-categories/{loser['id']}/identity", json=update(value="Cannot claim historical code", abbreviation="TEST-RACE")).status_code == 409
                    with pytest.raises(DBAPIError, match="reserved and immutable"):
                        with case.database.engine.begin() as connection:
                            connection.execute(text("UPDATE online_category_codes SET category_id=:id WHERE code='TEST-RACE'"), {"id": loser["id"]})
                    with pytest.raises(DBAPIError, match="reserved and immutable"):
                        with case.database.engine.begin() as connection:
                            connection.execute(text("DELETE FROM online_category_codes WHERE code='TEST-RACE'"))
                    with pytest.raises(DBAPIError, match="reserved and immutable"):
                        with case.database.engine.begin() as connection:
                            connection.execute(text("TRUNCATE online_category_codes"))
                    with pytest.raises(DBAPIError, match="reserved by another category"):
                        with case.database.engine.begin() as connection:
                            connection.execute(text("UPDATE online_categories SET abbreviation='TEST-RACE', version=version+1 WHERE id=:id"), {"id": loser["id"]})
                    with pytest.raises(DBAPIError, match="property changes must advance"):
                        with case.database.engine.begin() as connection:
                            connection.execute(text("UPDATE online_categories SET value='Unversioned correction' WHERE id=:id"), {"id": winner["id"]})
                    with pytest.raises(DBAPIError, match="Catalog identity is immutable"):
                        with case.database.engine.begin() as connection:
                            connection.execute(text("UPDATE online_categories SET created_at=created_at - interval '1 day' WHERE id=:id"), {"id": winner["id"]})
                command.check(config)
                with pytest.raises(DBAPIError, match="historical category code aliases"):
                    command.downgrade(config, "20261001_0040")
        finally:
            get_settings.cache_clear()


def test_alias_upgrade_recovers_multiple_identity_edits_without_rewriting_materials():
    with isolated_postgresql_database() as url, pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", url); get_settings.cache_clear()
        try:
            config = Config("alembic.ini"); command.upgrade(config, "20261001_0040")
            with contextmanager(_review_pg_case)(url) as case:
                with case.database.session() as session:
                    category = session.scalar(select(OnlineCategory).where(OnlineCategory.abbreviation == "G03"))
                    assert category is not None
                    category_id = category.id
                    for old, new, old_name, new_name in (("G03", "MIDDLE", "Facade / Tiles", "Middle label"), ("MIDDLE", "FINAL", "Middle label", "Final label")):
                        category.value = new_name
                        category.normalized_key = new_name.lower()
                        category.abbreviation = new
                        category.version += 1
                        session.flush()
                        session.add(CatalogAuditEvent(resource_id=category_id, resource_kind="CATEGORY", actor_id=case.users[0].id,
                            request_key=uuid4(), request_hash="a" * 64, result={"audit": {"property": "identity",
                                "before": {"abbreviation": old, "value": old_name}, "after": {"abbreviation": new, "value": new_name}}}))
                    session.commit()
                with case.database.engine.connect() as connection:
                    before = connection.execute(text("SELECT to_jsonb(m) FROM pbr_materials m ORDER BY id")).scalars().all()
                command.upgrade(config, "head"); command.check(config)
                with case.database.engine.connect() as connection:
                    assert connection.execute(text("SELECT to_jsonb(m) FROM pbr_materials m ORDER BY id")).scalars().all() == before
                    assert set(connection.execute(text("SELECT code FROM online_category_codes WHERE category_id=:id"), {"id": category_id}).scalars()) == {"G03", "MIDDLE", "FINAL"}
                with case.client_for() as client:
                    content = client.get(case.path + "/content").json()
                    assert content["required_category_id"] == str(category_id)
                    assert content["categories"][0]["value"] == "Final label"
        finally:
            get_settings.cache_clear()
