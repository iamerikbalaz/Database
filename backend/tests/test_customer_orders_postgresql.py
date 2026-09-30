"""Actual PostgreSQL serialization, legacy receipts, and safe directory migration."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError

from app.core.config import get_settings
from app.db.directory_models import DirectoryCommand
from app.db.models import ResourceCommand
from app.material_review import canonical_hash
from app.resource_commands import saved_response
from app.schemas import PublishedBrandRead
from test_materials_postgresql import (POSTGRES_TEST_ADMIN_URL, isolated_postgresql_database,
    migrated_postgresql_url, review_pg_case)  # noqa: F401

pytestmark = pytest.mark.skipif(POSTGRES_TEST_ADMIN_URL is None, reason="isolated PostgreSQL required")


def _customer(client):
    suffix = uuid4().hex[:12]
    response = client.post("/api/customers", json={"name": "PG customer " + suffix, "brand_identifier": "PG-" + suffix}, headers={"Idempotency-Key": str(uuid4())})
    assert response.status_code == 201, response.text
    return response.json()


def test_order_number_allocation_serializes_distinct_actors(review_pg_case):
    case = review_pg_case
    with case.client_for() as client:
        customer = _customer(client)
    barrier = Barrier(2)
    payload = {"customer_id": customer["id"], "project_type": "SCANNING_FABRICS", "starting_date": "2026-09-30"}
    def create(actor):
        with case.client_for(actor) as client:
            barrier.wait(timeout=15)
            return client.post("/api/orders", json=payload, headers={"Idempotency-Key": str(uuid4())})
    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(create, [0, 3]))
    assert [row.status_code for row in responses] == [201, 201], [row.text for row in responses]
    numbers = sorted(int(row.json()["number"]) for row in responses)
    assert numbers[1] == numbers[0] + 1
    assert all(row.json()["customer_id"] == customer["id"] for row in responses)


def test_directory_competing_edits_exact_replay_and_append_only_receipts(review_pg_case):
    case = review_pg_case
    with case.client_for() as client:
        customer = _customer(client)
    barrier = Barrier(2)
    keys = {actor: str(uuid4()) for actor in (0, 3)}
    payloads = {actor: {"expected_updated_at": customer["updated_at"], "notes": f"Actor {actor}"} for actor in (0, 3)}
    path = "/api/customers/" + customer["id"]
    def update(actor):
        with case.client_for(actor) as client:
            barrier.wait(timeout=15)
            return actor, client.patch(path, json=payloads[actor], headers={"Idempotency-Key": keys[actor]})
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(update, [0, 3]))
    assert sorted(row.status_code for _, row in results) == [200, 409]
    actor, successful = next(pair for pair in results if pair[1].status_code == 200)
    with case.client_for(actor) as client:
        replay = client.patch(path, json=payloads[actor], headers={"Idempotency-Key": keys[actor]})
        assert replay.json() == successful.json() and replay.headers["Idempotency-Replayed"] == "true"
        assert client.get("/api/directory-commands/" + keys[actor]).json()["response"] == successful.json()
    with case.database.session() as session:
        receipt = session.scalar(select(DirectoryCommand).where(DirectoryCommand.request_key == UUID(keys[actor])))
        receipt_id = receipt.id
    for statement in ("UPDATE directory_commands SET response_hash=repeat('a',64) WHERE id=:id", "DELETE FROM directory_commands WHERE id=:id"):
        with pytest.raises(DBAPIError), case.database.engine.begin() as connection:
            connection.execute(text(statement), {"id": receipt_id})


@pytest.mark.parametrize("kind,segment", [("BRAND", "brands"), ("PROJECT", "projects"), ("USER", "internal-users")])
def test_legacy_receipt_guard_ignores_only_new_directory_columns(review_pg_case, kind, segment):
    from test_resource_commands import creation_payload
    case = review_pg_case
    body = creation_payload(case.database, case.material, kind)
    key = str(uuid4())
    with case.client_for() as client:
        response = client.post("/api/" + segment, json=body, headers={"Idempotency-Key": key})
        assert response.status_code == 201, response.text
        assert client.post("/api/" + segment, json=body, headers={"Idempotency-Key": key}).json() == response.json()
        assert client.get("/api/resource-commands/" + key).json()["response"] == response.json()


def test_migration_preserves_prior_brand_receipt_and_refuses_losing_imported_profiles():
    with isolated_postgresql_database() as url, pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", url)
        get_settings.cache_clear()
        engine = create_engine(url)
        try:
            config = Config("alembic.ini")
            command.upgrade(config, "20260928_0033")
            actor, company, brand, project, receipt_id, key = [uuid4() for _ in range(6)]
            with engine.begin() as connection:
                connection.execute(text("INSERT INTO internal_users(id,display_name,email,role) VALUES(:id,'Prior actor','prior-directory@example.invalid','ADMIN')"), {"id": actor})
                connection.execute(text("INSERT INTO companies(id,name,address) VALUES(:id,'Prior company','Preserved address')"), {"id": company})
                connection.execute(text("INSERT INTO published_brands(id,company_id,name,folder_prefix,brand_identifier,next_sequence_number) VALUES(:id,:company,'Prior brand','PRIOR','P0001',84)"), {"id": brand, "company": company})
                connection.execute(text("INSERT INTO projects(id,company_id,project_number,name,status) VALUES(:id,:company,'0042','Prior folder','DONE')"), {"id": project, "company": company})
                old_row = dict(connection.execute(text("SELECT * FROM published_brands WHERE id=:id"), {"id": brand}).mappings().one())
                old_response = PublishedBrandRead.model_validate(old_row).model_dump(mode="json")
            from sqlalchemy.orm import Session
            with Session(engine) as session:
                session.add(ResourceCommand(id=receipt_id, actor_id=actor, request_key=key, kind="BRAND", action="UPDATED", privilege="CATALOG", brand_id=brand,
                    request_hash=canonical_hash({"legacy": True}), response_snapshot=old_response, response_hash=canonical_hash(old_response)))
                session.commit()
            command.upgrade(config, "head")
            command.check(config)
            with engine.connect() as connection:
                current = dict(connection.execute(text("SELECT * FROM published_brands WHERE id=:id"), {"id": brand}).mappings().one())
                assert all(current[field] == value for field, value in old_row.items())
                assert current["customer_brand_identifier"] == "P0001" and current["address"] == "Preserved address"
                order = connection.execute(text("SELECT customer_id,order_status FROM projects WHERE id=:id"), {"id": project}).one()
                assert order == (brand, "Done")
            with Session(engine) as session:
                assert saved_response(session.get(ResourceCommand, receipt_id)) == old_response
            # No new data yet: schema-only rehearsal can be reversed losslessly.
            command.downgrade(config, "20260928_0033")
            command.upgrade(config, "head")
            with engine.begin() as connection:
                connection.execute(text("UPDATE published_brands SET notes='Imported client note' WHERE id=:id"), {"id": brand})
            with pytest.raises(RuntimeError, match="Customers / Orders contain business data"):
                command.downgrade(config, "20260928_0033")
            with engine.connect() as connection:
                assert connection.scalar(text("SELECT notes FROM published_brands WHERE id=:id"), {"id": brand}) == "Imported client note"
        finally:
            engine.dispose()
            get_settings.cache_clear()
