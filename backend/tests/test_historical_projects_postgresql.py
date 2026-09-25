"""Migration compatibility and real FK integrity for projectless history."""
from contextlib import contextmanager

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from test_materials_postgresql import _review_pg_case, isolated_postgresql_database, migrated_postgresql_url, POSTGRES_TEST_ADMIN_URL

pytestmark = pytest.mark.skipif(POSTGRES_TEST_ADMIN_URL is None, reason="isolated PostgreSQL required")


def test_upgrade_preserves_0025_records_and_refuses_loss_of_projectless_materials():
    with isolated_postgresql_database() as url, pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", url)
        get_settings.cache_clear()
        try:
            config = Config("alembic.ini")
            command.upgrade(config, "20260919_0025")
            with contextmanager(_review_pg_case)(url) as case:
                with case.database.engine.connect() as connection:
                    with pytest.raises(IntegrityError):
                        connection.execute(text("UPDATE pbr_materials SET project_id=NULL WHERE id=:id"), {"id": case.material.id})
                    connection.rollback()
                command.upgrade(config, "head")
                command.current(config); command.heads(config); command.check(config)
                # A clean database can still cross the historical nullable-FK boundary.
                command.downgrade(config, "20260919_0025")
                command.upgrade(config, "head")
                with case.database.engine.begin() as connection:
                    connection.execute(text("UPDATE pbr_materials SET project_id=NULL WHERE id=:id"), {"id": case.material.id})
                with case.client_for() as client:
                    original = client.get(case.path).json()
                    assert original["project_id"] is None
                    assert original["technical_identity"] == case.material.technical_identity
                    assert original["folder_path"] == case.material.folder_path
                    with pytest.raises(RuntimeError, match="Assign projects"):
                        command.downgrade(config, "20260919_0025")
                    assert client.get(case.path).json()["project_id"] is None
                    restored = client.patch(case.path, json={"project_id": str(case.material.project_id)})
                    assert restored.status_code == 200
                    assert restored.json()["folder_path"] == original["folder_path"]
                # The new API has now recorded an additive tracking snapshot.
                # Rolling it back into an older history reader would lose meaning.
                with pytest.raises(RuntimeError, match="Material tracking history"):
                    command.downgrade(config, "20260919_0025")
                command.check(config)
        finally:
            get_settings.cache_clear()
