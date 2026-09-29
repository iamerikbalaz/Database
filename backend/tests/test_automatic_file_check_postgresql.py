"""Automatic check migration and exact material receipts on real PostgreSQL."""
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import DBAPIError

from app.core.config import get_settings
from app.db.models import MaterialAuditEvent, PBRMaterial, ResourceCommand
from app.main import create_app
from app.material_review import canonical_hash
from app.schemas import PBRMaterialRead
from test_materials_postgresql import _review_pg_case, isolated_postgresql_database, POSTGRES_TEST_ADMIN_URL
from test_application_access import ORIGIN, PASSWORD

pytestmark = pytest.mark.skipif(POSTGRES_TEST_ADMIN_URL is None, reason="isolated PostgreSQL required")


@pytest.mark.parametrize("human_checked", ["no", "Correction"])
def test_full_file_check_ok_survives_table_edit_and_exact_receipt_replay_without_human_approval(human_checked):
    with isolated_postgresql_database() as url, pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", url); get_settings.cache_clear()
        try:
            command.upgrade(Config("alembic.ini"), "head")
            with contextmanager(_review_pg_case)(url) as case:
                observed = []
                def check_many(folders):
                    observed.extend(folders)
                    return [{"profile": "PBR_FILES_V1", "complete": True, "issues": [],
                        "report": "Complete source check: no issues."} for _ in folders]
                library = SimpleNamespace(file_check_profile="PBR_FILES_V1", check_many=check_many)
                application = create_app(case.app.state.settings, case.database, local_library=library)
                with case.database.session() as session:
                    material = session.get(PBRMaterial, case.material.id)
                    material.checked_status = human_checked
                    session.commit()
                with TestClient(application, base_url=ORIGIN) as client:
                    login = client.post("/api/auth/login", json={"email": case.users[1].email, "password": PASSWORD},
                        headers={"Origin": ORIGIN})
                    assert login.status_code == 200
                    client.headers.update({"Origin": ORIGIN, "X-CSRF-Token": login.json()["csrf_token"]})
                    before = client.get(case.path).json()
                    human_fields = ("checked_status", "workflow_status", "validation_status", "is_published", "assigned_processor_id")
                    human_values = {key: before[key] for key in human_fields}
                    checked = client.post(case.path + "/check-data")
                    assert checked.status_code == 200, checked.text
                    assert (checked.json()["status"], checked.json()["profile"], checked.json()["complete"]) == ("OK", "PBR_FILES_V1", True)
                    current = client.get(case.path).json()
                    assert current["automatic_file_check_status"] == "OK"
                    assert current["automatic_file_check_profile"] == "PBR_FILES_V1"
                    assert current["automatic_file_check_complete"] is True
                    assert current["automatic_file_checked_at"] is not None
                    assert {key: current[key] for key in human_fields} == human_values
                    key = str(uuid4())
                    payload = {"expected_updated_at": current["updated_at"], "note": "A table note after the complete check"}
                    saved = client.patch(case.path + "/table", json=payload, headers={"Idempotency-Key": key})
                    assert saved.status_code == 200, saved.text
                    result = saved.json()
                    for field in ("automatic_file_check_status", "automatic_file_check_profile", "automatic_file_check_complete", "automatic_file_checked_at"):
                        assert result[field] == current[field]
                    assert {field: result[field] for field in human_fields} == human_values
                    assert "automatic_file_check_report" not in result
                    replay = client.patch(case.path + "/table", json=payload, headers={"Idempotency-Key": key})
                    assert replay.status_code == 200 and replay.json() == result
                    assert client.get("/api/resource-commands/" + key).json()["response"] == result
                    assert client.get(case.path).json() == result
                assert observed == [case.material.folder_path]
                with case.database.session() as session:
                    events = session.scalars(select(MaterialAuditEvent).where(
                        MaterialAuditEvent.material_id == case.material.id,
                        MaterialAuditEvent.event_type == "AUTOMATIC_FILE_CHECK")).all()
                    assert len(events) == 1 and events[0].actor_id == case.users[1].id
                    assert events[0].result["audit"]["profile"] == "PBR_FILES_V1"
                    assert events[0].result["audit"]["complete"] is True
                    material = session.get(PBRMaterial, case.material.id)
                    assert material.checked_status == human_checked
                    assert material.automatic_file_check_report == checked.json()["report"]
                    receipt = session.scalar(select(ResourceCommand).where(ResourceCommand.request_key == key))
                    original = {column.key: deepcopy(getattr(receipt, column.key)) for column in ResourceCommand.__table__.columns}
                # The database must bind all three certification fields to the
                # actual observation, even when a forged receipt hash is valid.
                for field, value in (("automatic_file_check_status", "ISSUES"),
                        ("automatic_file_check_profile", "BASIC_V1"), ("automatic_file_check_complete", False)):
                    forged = deepcopy(original)
                    forged.update(id=uuid4(), request_key=uuid4())
                    forged["response_snapshot"][field] = value
                    forged["response_hash"] = canonical_hash(forged["response_snapshot"])
                    with pytest.raises(DBAPIError, match="Resource command response must match the exact record"):
                        with case.database.session() as session:
                            session.add(ResourceCommand(**forged)); session.commit()
        finally:
            get_settings.cache_clear()


def test_0033_preserves_0032_receipts_and_accepts_exact_current_material_receipts():
    with isolated_postgresql_database() as url, pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", url); get_settings.cache_clear()
        try:
            config = Config("alembic.ini"); command.upgrade(config, "20260927_0032")
            with contextmanager(_review_pg_case)(url) as case:
                old_key = uuid4()
                with case.database.session() as session:
                    raw = dict(session.execute(text("SELECT * FROM pbr_materials WHERE id=:id"), {"id": case.material.id}).mappings().one())
                    old_response = PBRMaterialRead.model_validate(raw).model_dump(mode="json", exclude_unset=True)
                    assert "automatic_file_check_status" not in old_response
                    payload = {"material_name": old_response["material_name"]}
                    session.add(ResourceCommand(kind="MATERIAL", material_id=case.material.id, actor_id=case.users[0].id,
                        request_key=old_key, action="UPDATED", privilege="MATERIAL_NAME",
                        request_hash=canonical_hash({"schema_version": 1, "kind": "MATERIAL", "action": "UPDATED", "target_id": str(case.material.id), "payload": payload}),
                        response_snapshot=old_response, response_hash=canonical_hash(old_response)))
                    session.commit()
                command.upgrade(config, "head"); command.check(config)
                migration = ScriptDirectory.from_config(config).get_revision("20260928_0033").module
                with case.database.engine.begin() as connection:
                    migration.update_resource_command_guard(connection)
                    migration.update_resource_command_guard(connection)
                    definition = connection.scalar(text("SELECT pg_get_functiondef('resource_command_guard()'::regprocedure)"))
                    assert definition.count(migration._NEW_MATERIAL_KEYS) == 1
                    assert migration._OLD_MATERIAL_KEYS not in definition
                # Empty derived fields and old receipts allow a lossless rollback;
                # the SQL exact-response guard must revert with the columns.
                command.downgrade(config, "20260927_0032")
                assert "automatic_file_check_status" not in {item["name"] for item in inspect(case.database.engine).get_columns("pbr_materials")}
                command.upgrade(config, "head")
                # An early 0033 version emitted report-bearing receipts. Restore
                # its SQL shape and create one, then repair forward without
                # rewriting or invalidating that immutable response.
                intermediate_key = uuid4()
                with case.database.engine.begin() as connection:
                    definition = connection.scalar(text("SELECT pg_get_functiondef('resource_command_guard()'::regprocedure)"))
                    definition = definition.replace(migration._NEW_MATERIAL_KEYS, migration._INTERMEDIATE_MATERIAL_KEYS).replace(migration._NEW_MATERIAL_RESPONSE, migration._OLD_MATERIAL_RESPONSE)
                    connection.execute(text(definition))
                with case.database.session() as session:
                    material = session.get(PBRMaterial, case.material.id)
                    intermediate_response = {**PBRMaterialRead.model_validate(material).model_dump(mode="json"), "automatic_file_check_report": None}
                    session.add(ResourceCommand(kind="MATERIAL", material_id=case.material.id, actor_id=case.users[0].id,
                        request_key=intermediate_key, action="UPDATED", privilege="MATERIAL_NAME",
                        request_hash=canonical_hash({"schema_version": 1, "kind": "MATERIAL", "action": "UPDATED", "target_id": str(case.material.id), "payload": payload}),
                        response_snapshot=intermediate_response, response_hash=canonical_hash(intermediate_response)))
                    session.commit()
                with case.database.engine.begin() as connection:
                    migration.update_resource_command_guard(connection)
                    migration.update_resource_command_guard(connection)
                with case.client_for() as client:
                    replay = client.patch(case.path, json=payload, headers={"Idempotency-Key": str(old_key)})
                    assert replay.status_code == 200 and replay.json() == old_response
                    assert client.get("/api/resource-commands/" + str(old_key)).json()["response"] == old_response
                    intermediate_replay = client.patch(case.path, json=payload, headers={"Idempotency-Key": str(intermediate_key)})
                    assert intermediate_replay.status_code == 200 and intermediate_replay.json() == intermediate_response
                    assert client.get("/api/resource-commands/" + str(intermediate_key)).json()["response"] == intermediate_response
                    current = client.get(case.path).json()
                    key = str(uuid4()); body = {"expected_updated_at": current["updated_at"], "note": "Exact PG receipt"}
                    saved = client.patch(case.path + "/table", json=body, headers={"Idempotency-Key": key})
                    assert saved.status_code == 200, saved.text
                    assert saved.json()["automatic_file_check_status"] == "NOT_CHECKED"
                    assert saved.json()["automatic_file_check_complete"] is False
                    assert "automatic_file_check_report" not in saved.json()
                    assert client.patch(case.path + "/table", json=body, headers={"Idempotency-Key": key}).json() == saved.json()
                    assert client.get("/api/resource-commands/" + key).json()["response"] == saved.json()
                with case.database.session() as session:
                    receipt = session.scalar(select(ResourceCommand).where(ResourceCommand.request_key == key))
                    forged = {column.key: deepcopy(getattr(receipt, column.key)) for column in ResourceCommand.__table__.columns}
                    forged.update(id=uuid4(), request_key=uuid4())
                    forged["response_snapshot"]["automatic_file_check_status"] = "ISSUES"
                    forged["response_hash"] = canonical_hash(forged["response_snapshot"])
                with pytest.raises(DBAPIError, match="Resource command response must match the exact record"):
                    with case.database.session() as session:
                        session.add(ResourceCommand(**forged)); session.commit()
                # Reproduce the intermediate pre-release guard: new keys were
                # accepted, but checked_at compared JSON Z vs PostgreSQL +00:00.
                with case.database.engine.begin() as connection:
                    definition = connection.scalar(text("SELECT pg_get_functiondef('resource_command_guard()'::regprocedure)"))
                    assert definition.count(migration._NEW_RESPONSE_COMPARISON) == 1
                    connection.execute(text(definition.replace(migration._NEW_RESPONSE_COMPARISON, migration._OLD_RESPONSE_COMPARISON)))
                    connection.execute(text("UPDATE pbr_materials SET automatic_file_check_status='ISSUES', automatic_file_checked_at='2026-09-28T10:11:12.123456Z', automatic_file_check_report='Observed issue', automatic_file_check_profile='BASIC_V1' WHERE id=:id"), {"id": case.material.id})
                with case.client_for() as client:
                    current = client.get(case.path).json()
                    checked_key = str(uuid4()); checked_body = {"expected_updated_at": current["updated_at"], "note": "Edit after recorded check"}
                    failed = client.patch(case.path + "/table", json=checked_body, headers={"Idempotency-Key": checked_key})
                    assert failed.status_code == 503, failed.text
                # The forward-only helper must also repair a partially upgraded
                # guard, without altering any stored data or receipt.
                with case.database.engine.begin() as connection:
                    migration.update_resource_command_guard(connection)
                    migration.update_resource_command_guard(connection)
                with case.client_for() as client:
                    saved = client.patch(case.path + "/table", json=checked_body, headers={"Idempotency-Key": checked_key})
                    assert saved.status_code == 200, saved.text
                    assert saved.json()["automatic_file_checked_at"] == "2026-09-28T10:11:12.123456Z"
                    assert saved.json()["automatic_file_check_status"] == "ISSUES"
                    assert client.patch(case.path + "/table", json=checked_body, headers={"Idempotency-Key": checked_key}).json() == saved.json()
                    assert client.get("/api/resource-commands/" + checked_key).json()["response"] == saved.json()
                with case.database.session() as session:
                    receipt = session.scalar(select(ResourceCommand).where(ResourceCommand.request_key == checked_key))
                    forged = {column.key: deepcopy(getattr(receipt, column.key)) for column in ResourceCommand.__table__.columns}
                    forged.update(id=uuid4(), request_key=uuid4())
                    forged["response_snapshot"]["automatic_file_checked_at"] = "2026-09-28T10:11:13.123456Z"
                    forged["response_hash"] = canonical_hash(forged["response_snapshot"])
                with pytest.raises(DBAPIError, match="Resource command response must match the exact record"):
                    with case.database.session() as session:
                        session.add(ResourceCommand(**forged)); session.commit()
                # Preserve the earlier independent receipt-history downgrade
                # check even when no current automatic observation remains.
                with case.database.engine.begin() as connection:
                    connection.execute(text("UPDATE pbr_materials SET automatic_file_check_status='NOT_CHECKED', automatic_file_checked_at=NULL, automatic_file_check_report=NULL, automatic_file_check_profile=NULL WHERE id=:id"), {"id": case.material.id})
                with pytest.raises(RuntimeError, match="Automatic file check receipt history exists"):
                    command.downgrade(config, "20260927_0032")
                with case.database.engine.connect() as connection:
                    assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260928_0033"
        finally:
            get_settings.cache_clear()


def test_0033_preserves_material_data_defaults_and_refuses_erasing_recorded_checks():
    with isolated_postgresql_database() as url, pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", url); get_settings.cache_clear()
        try:
            config = Config("alembic.ini"); command.upgrade(config, "20260927_0032")
            with contextmanager(_review_pg_case)(url) as case:
                with case.database.engine.begin() as connection:
                    connection.execute(text("UPDATE pbr_materials SET workflow_status='DONE', checked_status='OK', is_published=true, note='Preserved' WHERE id=:id"), {"id": case.material.id})
                    before = dict(connection.execute(text("SELECT * FROM pbr_materials WHERE id=:id"), {"id": case.material.id}).mappings().one())
                command.upgrade(config, "head"); command.check(config)
                with case.database.session() as session:
                    material = session.get(PBRMaterial, case.material.id)
                    assert all(getattr(material, name) == value for name, value in before.items())
                    assert material.automatic_file_check_status == "NOT_CHECKED"
                    assert material.automatic_file_check_complete is False
                    assert material.automatic_file_checked_at is None and material.automatic_file_check_report is None
                with pytest.raises(DBAPIError), case.database.engine.begin() as connection:
                    connection.execute(text("UPDATE pbr_materials SET automatic_file_check_status='INVENTED' WHERE id=:id"), {"id": case.material.id})
                large_report = "Observed issue\n" + "Δ" * 100000
                with case.database.engine.begin() as connection:
                    connection.execute(text("UPDATE pbr_materials SET automatic_file_check_status='ISSUES', automatic_file_checked_at=now(), automatic_file_check_report=:report WHERE id=:id"), {"id": case.material.id, "report": large_report})
                with case.client_for() as client:
                    current = client.get(case.path).json()
                    assert "automatic_file_check_report" not in current
                    key = str(uuid4()); body = {"expected_updated_at": current["updated_at"], "note": "Editable after a large check report"}
                    saved = client.patch(case.path + "/table", json=body, headers={"Idempotency-Key": key})
                    assert saved.status_code == 200, saved.text
                    assert "automatic_file_check_report" not in saved.json()
                    assert saved.json()["automatic_file_check_status"] == "ISSUES"
                    assert client.patch(case.path + "/table", json=body, headers={"Idempotency-Key": key}).json() == saved.json()
                    assert client.get("/api/resource-commands/" + key).json()["response"] == saved.json()
                with pytest.raises(RuntimeError, match="Automatic file check results exist"):
                    command.downgrade(config, "20260927_0032")
                with case.database.engine.connect() as connection:
                    assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260928_0033"
                    assert connection.scalar(text("SELECT automatic_file_check_report FROM pbr_materials WHERE id=:id"), {"id": case.material.id}) == large_report
        finally:
            get_settings.cache_clear()
