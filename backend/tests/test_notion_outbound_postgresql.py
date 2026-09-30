"""Real lock-order and competing-insert checks in an isolated PostgreSQL DB."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import event, select

from app.db.models import Project, PublishedBrand
from app.db.notion_sync_models import NotionSyncState
from app.notion_outbound import enqueue_order_sync, ensure_customer_dependencies
from test_materials_postgresql import (review_pg_case, migrated_postgresql_url,
    POSTGRES_TEST_ADMIN_URL)  # noqa: F401

pytestmark = pytest.mark.skipif(POSTGRES_TEST_ADMIN_URL is None, reason="isolated PostgreSQL required")


def prepare(case):
    with case.database.session() as session:
        order = session.get(Project, case.material.project_id)
        order.customer_id = case.material.published_brand_id
        session.commit()
    return order.id, case.material.published_brand_id


def test_order_enqueue_does_not_wait_for_customer_row_lock(review_pg_case):
    case = review_pg_case
    order_id, customer_id = prepare(case)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with case.database.session() as customer_session:
            customer_session.scalar(select(PublishedBrand).where(PublishedBrand.id == customer_id).with_for_update())
            def enqueue():
                with case.database.session() as session:
                    order = session.scalar(select(Project).where(Project.id == order_id).with_for_update())
                    enqueue_order_sync(session, order, case.users[0].id)
                    session.commit()
            try:
                pool.submit(enqueue).result(timeout=5)
                # Customer→Order is the normal rename lock order. It remains free.
                customer_session.scalar(select(Project).where(Project.id == order_id).with_for_update(nowait=True))
            finally:
                customer_session.rollback()


def test_concurrent_dependency_seeding_creates_only_one_customer_job(review_pg_case):
    case = review_pg_case
    order_id, customer_id = prepare(case)
    with case.database.session() as session:
        enqueue_order_sync(session, session.get(Project, order_id), case.users[0].id)
        session.commit()
    barrier = Barrier(2)
    def after_query(connection, cursor, statement, parameters, context, executemany):
        if "LEFT OUTER JOIN notion_sync_states AS" in statement:
            barrier.wait(timeout=10)
    event.listen(case.database.engine, "after_cursor_execute", after_query)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(ensure_customer_dependencies, case.database) for _ in range(2)]
            for future in futures: future.result(timeout=15)
    finally:
        event.remove(case.database.engine, "after_cursor_execute", after_query)
    with case.database.session() as session:
        states = list(session.scalars(select(NotionSyncState).where(NotionSyncState.entity_type == "CUSTOMER",
            NotionSyncState.entity_id == customer_id)))
        assert len(states) == 1 and states[0].revision == 1 and states[0].status == "PENDING"
