"""A schema-only rollback is allowed; durable queue/history rows are retained."""
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import inspect, select

from app.db.models import Project, PublishedBrand
from app.notion_outbound import enqueue_customer_sync
from app.order_folders import enqueue_order_folder_create
from test_notion_outbound import sync_db  # noqa: F401


def migration(connection):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/20260930_0035_outbound_sync.py"
    spec = importlib.util.spec_from_file_location("outbound_migration_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.op = Operations(MigrationContext.configure(connection))
    return module


@pytest.mark.parametrize("kind", ["notion", "folder"])
def test_downgrade_refuses_to_discard_durable_work(sync_db, kind):
    db, actor, customer, order = sync_db
    with db.session() as session:
        if kind == "notion": enqueue_customer_sync(session, session.get(PublishedBrand, customer.id), actor.id)
        else: enqueue_order_folder_create(session, session.get(Project, order.id), actor.id)
        session.commit()
    with db.engine.begin() as connection:
        with pytest.raises(RuntimeError, match="durable work or history"):
            migration(connection).downgrade()
        assert inspect(connection).has_table("notion_sync_states")
        assert inspect(connection).has_table("order_folder_operations")


def test_downgrade_allows_empty_outbound_tables(sync_db):
    db, _, _, _ = sync_db
    with db.engine.begin() as connection:
        migration(connection).downgrade()
        assert not inspect(connection).has_table("notion_sync_states")
        assert not inspect(connection).has_table("order_folder_operations")
        assert inspect(connection).has_table("projects")
