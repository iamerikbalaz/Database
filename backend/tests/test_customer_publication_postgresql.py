"""Migration preserves receipts and refuses destructive publication downgrade."""
from uuid import uuid4
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from app.core.config import get_settings
from test_materials_postgresql import POSTGRES_TEST_ADMIN_URL, isolated_postgresql_database  # noqa: F401

pytestmark = pytest.mark.skipif(POSTGRES_TEST_ADMIN_URL is None, reason="isolated PostgreSQL required")


def test_customer_publication_upgrade_preserves_status_and_legacy_guard(monkeypatch):
    with isolated_postgresql_database() as url:
        monkeypatch.setenv("DATABASE_URL", url)
        get_settings.cache_clear()
        config = Config("alembic.ini")
        engine = create_engine(url)
        try:
            command.upgrade(config, "20260930_0036")
            company, brand = uuid4(), uuid4()
            with engine.begin() as connection:
                connection.execute(text("INSERT INTO companies(id,name) VALUES(:id,'Test')"), {"id": company})
                connection.execute(text("INSERT INTO published_brands(id,company_id,name,folder_prefix,brand_identifier,customer_status) VALUES(:id,:company,'Test','TEST','TEST','test')"), {"id": brand, "company": company})
            command.upgrade(config, "20261001_0037")
            with engine.connect() as connection:
                assert connection.execute(text("SELECT customer_status,is_published,country FROM published_brands WHERE id=:id"), {"id": brand}).one() == ("Test sample", False, None)
                assert "ARRAY['is_published','country']" in connection.scalar(text("SELECT pg_get_functiondef('resource_command_guard()'::regprocedure)"))
            command.downgrade(config, "20260930_0036")
            command.upgrade(config, "20261001_0037")
            with engine.begin() as connection:
                connection.execute(text("UPDATE published_brands SET is_published=true WHERE id=:id"), {"id": brand})
            with pytest.raises(RuntimeError, match="publication data"):
                command.downgrade(config, "20260930_0036")
        finally:
            engine.dispose()
            get_settings.cache_clear()
