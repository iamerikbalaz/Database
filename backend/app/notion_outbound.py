"""One-way property synchronization with a transactional, coalescing outbox.

No Notion page body is ever read into the application or replaced. A create with
an uncertain outcome is held for explicit reconciliation, never blindly retried.
"""
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import aliased

from app.db.notion_sync_models import NotionSyncState
from app.notion_reader import ORIGIN, VERSION, _private_io, page_id

CUSTOMER_FIELDS = {
    "name": ("Name", "title"), "customer_status": ("Status", "status"),
    "website": ("Website", "url"), "address": ("Address", "rich_text"),
    "shipping_address": ("Shipping address", "rich_text"), "legal_name": ("Legal name", "rich_text"),
    "vat_id": ("VAT ID", "rich_text"), "description": ("Company describtion", "rich_text"),
    "notes": ("Notes", "rich_text"), "brand_identifier": ("Brand Identifier", "rich_text"),
    "is_published": ("Published", "checkbox"),
}
ORDER_FIELDS = {
    "project_number": ("Number", "title"), "project_type": ("Project type", "rich_text"),
    "starting_date": ("Starting date", "date"), "due_date": ("Due date", "date"),
    "notes": ("Note", "rich_text"), "order_status": ("Status", "status"),
    "priority": ("Priority", "select"), "customer_page_id": ("Customer", "relation"),
    "responsible_page_ids": ("Responsible", "relation"),
}


class SyncError(RuntimeError):
    def __init__(self, code, *, ambiguous=False, retryable=False, delay=60):
        self.code, self.ambiguous, self.retryable, self.delay = code, ambiguous, retryable, delay
        super().__init__(code)


def _now(): return datetime.now(UTC)


def _snapshot(entity, fields):
    result = {}
    for field in fields:
        value = getattr(entity, field, None)
        result[field] = value.isoformat() if hasattr(value, "isoformat") else value
    return result


def _enqueue(session, kind, entity, actor_id, payload):
    session.flush()
    state = session.scalar(select(NotionSyncState).where(NotionSyncState.entity_type == kind,
        NotionSyncState.entity_id == entity.id).with_for_update())
    linked = getattr(entity, "notion_page_id", None)
    if state is None:
        state = NotionSyncState(entity_type=kind, entity_id=entity.id, actor_id=actor_id,
            page_id=page_id(linked) if linked else None, payload=payload, status="PENDING",
            revision=1, synced_revision=0, attempts=0)
        session.add(state)
    else:
        state.payload = payload
        state.actor_id = actor_id
        state.revision += 1
        # Edits cannot erase a create whose outcome is unknown or an active claim.
        if state.status not in {"RUNNING", "RECONCILE"}:
            state.status, state.error_code, state.next_attempt_at = "PENDING", None, None
        if linked and not state.page_id: state.page_id = page_id(linked)
    state.updated_at = _now()
    return state


def enqueue_customer_sync(session, customer, actor_id):
    payload = _snapshot(customer, CUSTOMER_FIELDS)
    payload["brand_identifier"] = getattr(customer, "customer_brand_identifier", None)
    return _enqueue(session, "CUSTOMER", customer, actor_id, payload)


def enqueue_order_sync(session, order, actor_id, *, responsible_changed=False):
    from app.db.models import InternalUser
    payload = _snapshot(order, set(ORDER_FIELDS) - {"customer_page_id", "responsible_page_ids"})
    payload["customer_id"] = str(order.customer_id) if order.customer_id else None
    person = session.get(InternalUser, order.responsible_id) if order.responsible_id else None
    people = list(getattr(order, "responsible_notion_page_ids", None) or [])
    mapped = getattr(person, "notion_people_page_id", None)
    if responsible_changed: people = [mapped] if mapped else []
    elif not people and mapped: people = [mapped]
    payload["responsible_page_ids"] = people
    payload["responsible_unmapped"] = bool(responsible_changed and person and not mapped)
    payload["skip_responsible"] = bool(not responsible_changed and person and not people)
    return _enqueue(session, "ORDER", order, actor_id, payload)


def ensure_customer_dependencies(database):
    """Seed missing dependencies before taking any order/entity/queue lock.

Doing this while an Order edit holds its row lock can deadlock a Customer rename
which locks Customer then Orders. Even INSERT ON CONFLICT may wait for another
transaction, so it belongs in this separate short transaction, before claiming.
"""
    from app.db.models import Project, PublishedBrand
    with database.session() as session:
        order_state, customer_state = aliased(NotionSyncState), aliased(NotionSyncState)
        missing = session.execute(select(PublishedBrand, order_state.actor_id)
            .join(Project, Project.customer_id == PublishedBrand.id)
            .join(order_state, and_(order_state.entity_type == "ORDER", order_state.entity_id == Project.id))
            .outerjoin(customer_state, and_(customer_state.entity_type == "CUSTOMER",
                customer_state.entity_id == PublishedBrand.id))
            .where(order_state.status == "PENDING", PublishedBrand.notion_page_id.is_(None), customer_state.id.is_(None))
            .order_by(PublishedBrand.id, order_state.updated_at, order_state.id).limit(100)).all()
        customers = {}
        for customer, actor_id in missing: customers.setdefault(customer.id, (customer, actor_id))
        if not customers: return
        dialect = session.get_bind().dialect.name
        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        elif dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert
        else: raise RuntimeError("Outbound queue requires PostgreSQL or SQLite")
        for identifier, (customer, actor_id) in sorted(customers.items()):
            payload = _snapshot(customer, CUSTOMER_FIELDS)
            payload["brand_identifier"] = customer.customer_brand_identifier
            session.execute(insert(NotionSyncState).values(id=uuid4(), entity_type="CUSTOMER",
                entity_id=identifier, actor_id=actor_id, page_id=None, payload=payload, revision=1,
                synced_revision=0, status="PENDING", attempts=0, updated_at=_now())
                .on_conflict_do_nothing(index_elements=["entity_type", "entity_id"]))
        session.commit()


def sync_status(session, entity_type, entity_id):
    state = session.scalar(select(NotionSyncState).where(NotionSyncState.entity_type == entity_type.upper(),
        NotionSyncState.entity_id == entity_id))
    if state is None: return {"status": "NOT_QUEUED", "error_code": None, "synced_at": None, "page_id": None}
    return {"status": state.status, "error_code": state.error_code, "synced_at": state.synced_at,
        "page_id": state.page_id, "pending_changes": state.revision != state.synced_revision}


def notion_properties(kind, payload):
    fields = CUSTOMER_FIELDS if kind == "CUSTOMER" else ORDER_FIELDS
    result = {}
    for field, (name, property_type) in fields.items():
        if field == "responsible_page_ids" and payload.get("skip_responsible"): continue
        value = payload.get(field)
        if field == "is_published" and field not in payload:
            # Old outbox snapshots must not clear a newer Published flag.
            continue
        if field == "customer_status":
            value = {"Active": "Active cooperation", "test": "Test sample", "In library": "In library (not verified)"}.get(value, value)
        if property_type in {"rich_text", "title"}:
            value = value or ""
            if not isinstance(value, str) or len(value) > 200000: raise SyncError("NOTION_TEXT_TOO_LONG")
            encoded = [{"type": "text", "text": {"content": value[offset:offset + 2000]}}
                for offset in range(0, len(value), 2000)]
        elif property_type == "date": encoded = {"start": value} if value else None
        elif property_type == "checkbox": encoded = bool(value)
        elif property_type in {"select", "status"}: encoded = {"name": value} if value else None
        elif property_type == "relation":
            values = value if isinstance(value, list) else [value] if value else []
            encoded = [{"id": page_id(identifier)} for identifier in values]
        else: encoded = value or None
        result[name] = {property_type: encoded}
    return result


class NotionWriter:
    def __init__(self, settings, *, transport=None):
        self.settings, self.transport = settings, transport

    async def write(self, kind, payload, linked_page_id):
        if not self.settings.notion_outbound_enabled: raise SyncError("NOTION_OUTBOUND_DISABLED")
        source = (self.settings.notion_customers_data_source_id if kind == "CUSTOMER"
            else self.settings.notion_orders_data_source_id)
        source = page_id(source)
        properties = notion_properties(kind, payload)
        private = _private_io.set(True)
        token = self.settings.notion_access_token.get_secret_value()
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=self.settings.notion_timeout_seconds,
                    follow_redirects=False, trust_env=False, headers={"Authorization": "Bearer " + token,
                    "Notion-Version": VERSION, "Accept": "application/json", "Accept-Encoding": "identity"}) as client:
                async def request(method, path, body=None, *, create=False):
                    try:
                        client.cookies.clear()
                        async with client.stream(method, ORIGIN + path, json=body) as response:
                            if response.status_code == 429:
                                raw_delay = response.headers.get("Retry-After", "60")
                                delay = min(3600, max(1, int(raw_delay))) if raw_delay.isdecimal() else 60
                                raise SyncError("NOTION_RATE_LIMITED", retryable=True, delay=delay)
                            if response.status_code in {401, 403}: raise SyncError("NOTION_ACCESS_DENIED")
                            if response.status_code == 404: raise SyncError("NOTION_PAGE_UNAVAILABLE")
                            if response.status_code >= 500:
                                raise SyncError("NOTION_CREATE_UNCERTAIN" if create else "NOTION_UNAVAILABLE",
                                    ambiguous=create, retryable=not create)
                            if response.status_code not in {200, 201}: raise SyncError("NOTION_REQUEST_REJECTED")
                            raw = bytearray()
                            async for part in response.aiter_bytes():
                                raw.extend(part)
                                if len(raw) > 2 * 1024**2:
                                    raise SyncError("NOTION_RESPONSE_INVALID", ambiguous=create)
                            import json
                            try: result = json.loads(raw)
                            except (ValueError, TypeError): raise SyncError("NOTION_RESPONSE_INVALID", ambiguous=create) from None
                            if not isinstance(result, dict): raise SyncError("NOTION_RESPONSE_INVALID", ambiguous=create)
                            return result
                    except httpx.ConnectError: raise SyncError("NOTION_UNAVAILABLE", retryable=True) from None
                    except httpx.HTTPError:
                        raise SyncError("NOTION_CREATE_UNCERTAIN" if create else "NOTION_UNAVAILABLE",
                            ambiguous=create, retryable=not create) from None

                schema = await request("GET", "/v1/data_sources/" + source)
                for name, value in properties.items():
                    prop = schema.get("properties", {}).get(name, {})
                    if prop.get("type") != next(iter(value)): raise SyncError("NOTION_SCHEMA_CHANGED")
                if linked_page_id:
                    linked_page_id = page_id(linked_page_id)
                    page = await request("GET", "/v1/pages/" + linked_page_id)
                    try: parent_id = page_id(page.get("parent", {}).get("data_source_id"))
                    except Exception: raise SyncError("NOTION_PAGE_PARENT_MISMATCH") from None
                    if (parent_id != source
                            or page.get("archived") or page.get("in_trash")):
                        raise SyncError("NOTION_PAGE_PARENT_MISMATCH")
                    result = await request("PATCH", "/v1/pages/" + linked_page_id, {"properties": properties})
                else:
                    key, kind_key = ("Brand Identifier", "rich_text") if kind == "CUSTOMER" else ("Number", "title")
                    value = payload.get("brand_identifier" if kind == "CUSTOMER" else "project_number")
                    if kind == "CUSTOMER" and not value:
                        key, kind_key, value = "Name", "title", payload.get("name")
                    if not value: raise SyncError("NOTION_MATCH_KEY_REQUIRED")
                    matches = await request("POST", "/v1/data_sources/" + source + "/query",
                        {"filter": {"property": key, kind_key: {"equals": value}}, "page_size": 2})
                    if not isinstance(matches.get("results"), list): raise SyncError("NOTION_RESPONSE_INVALID")
                    if matches["results"] or matches.get("has_more"):
                        # Existing data must be explicitly linked by bootstrap/reconciliation.
                        raise SyncError("NOTION_EXISTING_RECORD_REQUIRES_LINK")
                    result = await request("POST", "/v1/pages", {"parent": {"type": "data_source_id",
                        "data_source_id": source}, "properties": properties}, create=True)
                try: result_id = page_id(result.get("id"))
                except Exception: raise SyncError("NOTION_RESPONSE_INVALID", ambiguous=not linked_page_id) from None
                if linked_page_id and result_id != linked_page_id: raise SyncError("NOTION_RESPONSE_INVALID")
                return result_id
        finally:
            _private_io.reset(private)


async def process_next_sync(database, settings, *, writer=None):
    """Claim/commit, external write, then acknowledge. Returns False if no work."""
    if not settings.notion_outbound_enabled: return False
    ensure_customer_dependencies(database)
    from app.db.models import InternalUser, PublishedBrand, Project
    with database.session() as session:
        state = session.scalar(select(NotionSyncState).where(NotionSyncState.status == "PENDING",
            or_(NotionSyncState.next_attempt_at.is_(None), NotionSyncState.next_attempt_at <= _now()))
            .order_by(NotionSyncState.entity_type, NotionSyncState.updated_at)
            .with_for_update(skip_locked=True).limit(1))
        if state is None: return False
        actor = session.get(InternalUser, state.actor_id)
        if actor is None or not actor.is_active or actor.role not in {"ADMIN", "PRODUCTION_LEAD"}:
            state.status, state.error_code = "ERROR", "NOTION_SYNC_ACTOR_NOT_ALLOWED"
            session.commit(); return True
        payload = dict(state.payload)
        if state.entity_type == "ORDER":
            customer = session.get(PublishedBrand, UUID(payload["customer_id"])) if payload.get("customer_id") else None
            customer_state = session.scalar(select(NotionSyncState).where(NotionSyncState.entity_type == "CUSTOMER",
                NotionSyncState.entity_id == customer.id)) if customer else None
            payload["customer_page_id"] = (customer_state.page_id if customer_state else None) or getattr(customer, "notion_page_id", None)
            if not payload["customer_page_id"]:
                state.error_code, state.next_attempt_at = "NOTION_CUSTOMER_PENDING", _now() + timedelta(seconds=30)
                session.commit(); return True
            if payload.get("responsible_unmapped"):
                state.status, state.error_code = "ERROR", "NOTION_RESPONSIBLE_MAPPING_REQUIRED"
                session.commit(); return True
        state.status, state.started_at, state.error_code = "RUNNING", _now(), None
        state.attempts += 1
        claim = (state.id, state.revision, state.entity_type, state.page_id, state.entity_id)
        session.commit()
    try:
        result_id = await (writer or NotionWriter(settings)).write(claim[2], payload, claim[3])
        failure = None
    except SyncError as error: result_id, failure = None, error
    except Exception:
        # A programming/transport failure after a create claim is conservative.
        result_id, failure = None, SyncError("NOTION_CREATE_UNCERTAIN" if not claim[3] else "NOTION_UNAVAILABLE",
            ambiguous=not claim[3], retryable=bool(claim[3]))
    with database.session() as session:
        # Directory edits lock their entity before its outbox row. Preserve the
        # same order here so acknowledging a write cannot deadlock a concurrent edit.
        model = PublishedBrand if claim[2] == "CUSTOMER" else Project
        entity = session.scalar(select(model).where(model.id == claim[4]).with_for_update())
        state = session.scalar(select(NotionSyncState).where(NotionSyncState.id == claim[0]).with_for_update())
        if state is None or state.status != "RUNNING": return True
        if failure:
            state.error_code = failure.code
            state.status = "RECONCILE" if failure.ambiguous else "PENDING" if failure.retryable else "ERROR"
            state.next_attempt_at = _now() + timedelta(seconds=failure.delay) if failure.retryable else None
        else:
            state.page_id, state.synced_revision, state.synced_at = result_id, claim[1], _now()
            state.status = "SYNCED" if state.revision == claim[1] else "PENDING"
            state.error_code, state.next_attempt_at = None, None
            if entity is not None: entity.notion_page_id = result_id
        state.updated_at = _now()
        session.commit()
    return True


def recover_interrupted_sync(database):
    """Called once before the sole dispatcher starts; no create replay on crash."""
    with database.session() as session:
        for state in session.scalars(select(NotionSyncState).where(NotionSyncState.status == "RUNNING").with_for_update()):
            state.status = "PENDING" if state.page_id else "RECONCILE"
            state.error_code = "NOTION_INTERRUPTED_UPDATE" if state.page_id else "NOTION_CREATE_UNCERTAIN"
        session.commit()
