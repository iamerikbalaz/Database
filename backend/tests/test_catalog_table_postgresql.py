"""Catalog code migration preserves canonical names and historical versions."""
from contextlib import contextmanager

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.config import get_settings
from app.texture_categories import CATEGORIES
from test_catalog_table import update
from test_materials_postgresql import (_review_pg_case, isolated_postgresql_database,
                                      POSTGRES_TEST_ADMIN_URL)  # noqa: F401

pytestmark = pytest.mark.skipif(POSTGRES_TEST_ADMIN_URL is None, reason="isolated PostgreSQL required")


def test_catalog_code_upgrade_preserves_values_versions_and_guards_history():
    with isolated_postgresql_database() as url, pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", url); get_settings.cache_clear()
        try:
            config = Config("alembic.ini"); command.upgrade(config, "20260926_0028")
            with contextmanager(_review_pg_case)(url) as case:
                with case.database.engine.connect() as connection:
                    before = {row["id"]: dict(row) for row in connection.execute(text("SELECT * FROM online_categories")).mappings()}
                command.upgrade(config, "head"); command.check(config)
                with case.database.engine.connect() as connection:
                    after = [dict(row) for row in connection.execute(text("SELECT * FROM online_categories")).mappings()]
                assert len(after) == len(before) == 75
                codes = {entry["value"]: entry["code"] for entry in CATEGORIES}
                for row in after:
                    assert row.pop("abbreviation") == codes[row["value"]]
                    assert row == before[row["id"]]
                command.downgrade(config, "20260926_0028")
                command.upgrade(config, "head"); command.check(config)
                category_id = after[0]["id"]
                with pytest.raises(DBAPIError, match="Catalog identity is immutable"):
                    with case.database.engine.begin() as connection:
                        connection.execute(text("UPDATE online_categories SET value='Unauthorized rename' WHERE id=:id"), {"id": category_id})
                with pytest.raises(DBAPIError, match="property changes must advance"):
                    with case.database.engine.begin() as connection:
                        connection.execute(text("UPDATE online_categories SET abbreviation='UNVERSIONED' WHERE id=:id"), {"id": category_id})
                with case.client_for() as client:
                    payload = update(abbreviation="NEW_CODE")
                    path = f"/api/online-categories/{category_id}/table"
                    saved = client.patch(path, json=payload)
                    assert saved.status_code == 200, saved.text
                    assert saved.json()["abbreviation"] == "NEW_CODE"
                    assert client.patch(path, json=payload).json() == saved.json()
                with pytest.raises(RuntimeError, match="Catalog abbreviation"):
                    command.downgrade(config, "20260926_0028")
        finally:
            get_settings.cache_clear()
