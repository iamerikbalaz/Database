"""No network or NAS writes: transport fakes and isolated SQLite state."""
import asyncio
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.core.config import Settings
from app.db.base import Base
from app.db.models import Company, InternalUser, Project, PublishedBrand
from app.db.notion_sync_models import NotionSyncState
from app.db.session import Database
from app.notion_outbound import (CUSTOMER_FIELDS, ORDER_FIELDS, NotionWriter, SyncError,
    enqueue_customer_sync, enqueue_order_sync, ensure_customer_dependencies, notion_properties,
    process_next_sync, recover_interrupted_sync)


@pytest.fixture
def sync_db():
    database = Database("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(database.engine)
    with database.session() as session:
        actor = InternalUser(display_name="Operator", email="operator@example.invalid", role="ADMIN")
        company = Company(name="Customer")
        session.add_all([actor, company]); session.flush()
        customer = PublishedBrand(company_id=company.id, name="Acme brand", folder_prefix="ACME",
            brand_identifier="internal-placeholder", customer_brand_identifier="acme", customer_status="Active cooperation")
        session.add(customer); session.flush()
        order = Project(company_id=company.id, customer_id=customer.id, project_number="0253",
            name="0253_ACME BRAND_SCANNING_FABRICS_092026", project_type="SCANNING_FABRICS",
            starting_date=date(2026, 9, 30), order_status="Not started")
        session.add(order); session.commit()
    yield database, actor, customer, order
    database.dispose()


def settings():
    return Settings(_env_file=None, notion_outbound_enabled=True, notion_access_token=SecretStr("test_" + "x" * 40))


def customer_payload():
    return dict(name="Acme", is_published=False, customer_status="Active cooperation", brand_identifier="acme", notes="X" * 2100)


def test_properties_preserve_notion_types_chunks_and_excluded_fields():
    props = notion_properties("CUSTOMER", customer_payload())
    assert len(props["Notes"]["rich_text"]) == 2
    assert set(props) == {value[0] for value in CUSTOMER_FIELDS.values()}
    assert "Phone" not in props and "RWT Categories" not in props
    assert props["Status"] == {"status": {"name": "Active cooperation"}}
    people = [str(uuid4()), str(uuid4())]
    props = notion_properties("ORDER", {"responsible_page_ids": people})
    assert props["Responsible"] == {"relation": [{"id": value} for value in people]}
    assert "Generated Name" not in props and "Cutomer rollup" not in props
    assert "Responsible" not in notion_properties("ORDER", {"skip_responsible": True})


def transport_factory(*, existing=False, ambiguous=False, parent_wrong=False, throttle=False):
    calls = []
    remote_id = str(uuid4())
    config = settings()
    def handler(request):
        calls.append(request)
        if request.url.path.startswith("/v1/data_sources/") and request.method == "GET":
            return httpx.Response(200, json={"properties": {name: {"type": kind} for name, kind in CUSTOMER_FIELDS.values()}})
        if request.url.path.endswith("/query"):
            return httpx.Response(200, json={"results": [{"id": remote_id}] if existing else [], "has_more": False})
        if request.method == "GET":
            return httpx.Response(200, json={"parent": {"data_source_id": str(uuid4()) if parent_wrong else config.notion_customers_data_source_id}})
        if throttle: return httpx.Response(429, headers={"Retry-After": "12"})
        if ambiguous: raise httpx.ReadTimeout("Do not expose request credentials", request=request)
        return httpx.Response(200, json={"id": remote_id})
    return httpx.MockTransport(handler), calls, remote_id


def test_create_writes_properties_only_and_checks_existing_key():
    transport, calls, remote = transport_factory()
    result = asyncio.run(NotionWriter(settings(), transport=transport).write("CUSTOMER", customer_payload(), None))
    assert result == remote
    import json
    posted = json.loads(calls[-1].content)
    assert set(posted) == {"parent", "properties"}
    assert posted["parent"]["data_source_id"] == settings().notion_customers_data_source_id
    assert [call.method for call in calls] == ["GET", "POST", "POST"]
    assert all(str(call.url).startswith("https://api.notion.com/") for call in calls)


@pytest.mark.parametrize("options,code,ambiguous,retryable", [
    ({"existing": True}, "NOTION_EXISTING_RECORD_REQUIRES_LINK", False, False),
    ({"ambiguous": True}, "NOTION_CREATE_UNCERTAIN", True, False),
    ({"throttle": True}, "NOTION_RATE_LIMITED", False, True),
])
def test_create_safe_failure_classification(options, code, ambiguous, retryable):
    transport, calls, _ = transport_factory(**options)
    with pytest.raises(SyncError) as caught:
        asyncio.run(NotionWriter(settings(), transport=transport).write("CUSTOMER", customer_payload(), None))
    assert caught.value.code == code and caught.value.ambiguous == ambiguous and caught.value.retryable == retryable
    if options.get("existing"): assert calls[-1].url.path.endswith("/query")


def test_linked_update_rejects_foreign_parent_without_writing():
    transport, calls, remote = transport_factory(parent_wrong=True)
    with pytest.raises(SyncError, match="NOTION_PAGE_PARENT_MISMATCH"):
        asyncio.run(NotionWriter(settings(), transport=transport).write("CUSTOMER", customer_payload(), remote))
    assert all(call.method == "GET" for call in calls)


def test_blank_identifier_create_uses_name_without_exporting_placeholder():
    transport, calls, _ = transport_factory()
    payload = customer_payload(); payload["brand_identifier"] = None
    asyncio.run(NotionWriter(settings(), transport=transport).write("CUSTOMER", payload, None))
    import json
    assert json.loads(calls[1].content)["filter"] == {"property": "Name", "title": {"equals": "Acme"}}
    assert json.loads(calls[-1].content)["properties"]["Brand Identifier"] == {"rich_text": []}


def test_linked_update_sends_no_page_body_or_parent():
    transport, calls, remote = transport_factory()
    asyncio.run(NotionWriter(settings(), transport=transport).write("CUSTOMER", customer_payload(), remote))
    import json
    assert calls[-1].method == "PATCH"
    assert set(json.loads(calls[-1].content)) == {"properties"}


def test_customer_snapshot_never_exports_internal_identifier(sync_db):
    db, actor, customer, _ = sync_db
    with db.session() as session:
        customer = session.get(PublishedBrand, customer.id)
        customer.customer_brand_identifier = None
        state = enqueue_customer_sync(session, customer, actor.id)
        assert state.payload["brand_identifier"] is None


def test_order_snapshot_preserves_multiple_people_until_explicit_change(sync_db):
    db, actor, _, order = sync_db
    people = [str(uuid4()), str(uuid4())]
    with db.session() as session:
        order = session.get(Project, order.id)
        order.responsible_notion_page_ids = people
        state = enqueue_order_sync(session, order, actor.id)
        assert state.payload["responsible_page_ids"] == people
        state = enqueue_order_sync(session, order, actor.id, responsible_changed=True)
        assert state.payload["responsible_page_ids"] == []


def test_ambiguous_create_is_not_retried_by_edits_or_dispatcher(sync_db):
    db, actor, customer, _ = sync_db
    with db.session() as session:
        enqueue_customer_sync(session, session.get(PublishedBrand, customer.id), actor.id); session.commit()
    class FakeWriter:
        count = 0
        async def write(self, *args):
            self.count += 1
            raise SyncError("NOTION_CREATE_UNCERTAIN", ambiguous=True)
    writer = FakeWriter()
    assert asyncio.run(process_next_sync(db, settings(), writer=writer))
    with db.session() as session:
        customer = session.get(PublishedBrand, customer.id)
        customer.name = "Edited"
        state = enqueue_customer_sync(session, customer, actor.id)
        assert state.status == "RECONCILE"
        session.commit()
    assert not asyncio.run(process_next_sync(db, settings(), writer=writer))
    assert writer.count == 1


def test_concurrent_edits_remain_pending_after_successful_older_snapshot(sync_db):
    db, actor, customer, _ = sync_db
    with db.session() as session:
        enqueue_customer_sync(session, session.get(PublishedBrand, customer.id), actor.id); session.commit()
    remote = str(uuid4())
    class FakeWriter:
        async def write(self, kind, payload, linked):
            with db.session() as session:
                changed = session.get(PublishedBrand, customer.id)
                changed.name = "New name"
                enqueue_customer_sync(session, changed, actor.id); session.commit()
            return remote
    asyncio.run(process_next_sync(db, settings(), writer=FakeWriter()))
    with db.session() as session:
        state = session.scalar(select(NotionSyncState))
        assert state.status == "PENDING" and state.revision == 2 and state.synced_revision == 1
        assert state.page_id == remote and session.get(PublishedBrand, customer.id).notion_page_id == remote


def test_order_waits_for_customer_and_disabled_dispatcher_does_nothing(sync_db):
    db, actor, customer, order = sync_db
    with db.session() as session:
        customer_state = enqueue_customer_sync(session, session.get(PublishedBrand, customer.id), actor.id)
        customer_state.status = "RECONCILE"
        enqueue_order_sync(session, session.get(Project, order.id), actor.id); session.commit()
    class NeverWriter:
        async def write(self, *args): raise AssertionError("Unexpected IO")
    assert not asyncio.run(process_next_sync(db, Settings(_env_file=None), writer=NeverWriter()))
    assert asyncio.run(process_next_sync(db, settings(), writer=NeverWriter()))
    with db.session() as session:
        state = session.scalar(select(NotionSyncState).where(NotionSyncState.entity_type == "ORDER"))
        assert state.error_code == "NOTION_CUSTOMER_PENDING" and state.status == "PENDING"


def test_order_dispatch_enqueues_unlinked_legacy_customer_and_dispatches_dependency_first(sync_db):
    db, actor, customer, order = sync_db
    with db.session() as session:
        assert not list(session.scalars(select(NotionSyncState)))
        enqueue_order_sync(session, session.get(Project, order.id), actor.id)
        session.commit()
        # Seeding happens after the caller's Order transaction, avoiding reverse locks.
        assert [state.entity_type for state in session.scalars(select(NotionSyncState))] == ["ORDER"]
    calls = []
    customer_page, order_page = str(uuid4()), str(uuid4())
    class FakeWriter:
        async def write(self, kind, payload, linked):
            calls.append((kind, payload, linked))
            return customer_page if kind == "CUSTOMER" else order_page
    writer = FakeWriter()
    assert asyncio.run(process_next_sync(db, settings(), writer=writer))
    assert asyncio.run(process_next_sync(db, settings(), writer=writer))
    assert [call[0] for call in calls] == ["CUSTOMER", "ORDER"]
    assert calls[1][1]["customer_page_id"] == customer_page
    with db.session() as session:
        assert session.get(PublishedBrand, customer.id).notion_page_id == customer_page
        assert session.get(Project, order.id).notion_page_id == order_page
        assert all(state.status == "SYNCED" for state in session.scalars(select(NotionSyncState)))


@pytest.mark.parametrize("status", ["PENDING", "ERROR", "RUNNING", "RECONCILE"])
def test_order_enqueue_preserves_existing_customer_outbox_state(sync_db, status):
    db, actor, customer, order = sync_db
    with db.session() as session:
        state = enqueue_customer_sync(session, session.get(PublishedBrand, customer.id), actor.id)
        state.status, state.error_code = status, "TEST_EXISTING_STATE"
        session.commit()
        session.refresh(state)
        original = (state.revision, state.payload.copy(), state.actor_id, state.updated_at)
        enqueue_order_sync(session, session.get(Project, order.id), actor.id)
        session.commit()
        ensure_customer_dependencies(db)
        session.refresh(state)
        assert (state.status, state.error_code) == (status, "TEST_EXISTING_STATE")
        assert (state.revision, state.payload, state.actor_id, state.updated_at) == original
        assert len(list(session.scalars(select(NotionSyncState).where(NotionSyncState.entity_type == "CUSTOMER")))) == 1


def test_order_does_not_enqueue_already_linked_customer(sync_db):
    db, actor, customer, order = sync_db
    with db.session() as session:
        session.get(PublishedBrand, customer.id).notion_page_id = str(uuid4())
        enqueue_order_sync(session, session.get(Project, order.id), actor.id)
        session.commit()
        ensure_customer_dependencies(db)
        assert [state.entity_type for state in session.scalars(select(NotionSyncState))] == ["ORDER"]


def test_crash_recovery_never_replays_unacknowledged_create(sync_db):
    db, actor, customer, _ = sync_db
    with db.session() as session:
        state = enqueue_customer_sync(session, session.get(PublishedBrand, customer.id), actor.id)
        state.status = "RUNNING"; session.commit()
    recover_interrupted_sync(db)
    with db.session() as session:
        assert session.scalar(select(NotionSyncState)).status == "RECONCILE"


def test_revoked_actor_does_not_dispatch_queued_external_write(sync_db):
    db, actor, customer, _ = sync_db
    with db.session() as session:
        enqueue_customer_sync(session, session.get(PublishedBrand, customer.id), actor.id)
        session.get(InternalUser, actor.id).is_active = False
        session.commit()
    class NeverWriter:
        async def write(self, *args): raise AssertionError("Unexpected IO")
    asyncio.run(process_next_sync(db, settings(), writer=NeverWriter()))
    with db.session() as session:
        assert session.scalar(select(NotionSyncState)).error_code == "NOTION_SYNC_ACTOR_NOT_ALLOWED"
