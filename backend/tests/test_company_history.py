from uuid import uuid4

import pytest
from sqlalchemy import select

from app.company_history import append_company_change, company_snapshot
from app.db.models import Company, CompanyChangeEvent, ImmutableAuditSnapshotError
from app.material_review import canonical_hash
from test_application_access import access_case


def record(case, name="First audited company"):
    with case.database.session() as session:
        company = Company(name=name); session.add(company)
        event = append_company_change(session, company, case.users["ADMIN"].id, {}, action="CREATED")
        session.commit(); return company.id, event.id


def test_history_records_exact_whitelisted_snapshots_atomically(access_case):
    case = access_case; identifier, first = record(case)
    with case.database.session() as session:
        company = session.scalar(select(Company).where(Company.id == identifier).with_for_update())
        before = company_snapshot(company); company.legal_name = "Synthetic legal name"
        changed = append_company_change(session, company, case.users["ADMIN"].id, before)
        assert changed.version == 2 and changed.before_hash == canonical_hash(before)
        assert changed.after_snapshot["legal_name"] == "Synthetic legal name"
        assert set(changed.after_snapshot) == {"id", "name", "legal_name", "country", "address", "website", "vat_id", "notion_page_id", "is_active"}
        assert not changed.source and changed.request_key is None
        session.commit()
    with case.database.session() as session:
        assert session.get(CompanyChangeEvent, first).before_snapshot == {}
        company = session.get(Company, identifier)
        assert append_company_change(session, company, case.users["ADMIN"].id, company_snapshot(company)) is None
        assert len(list(session.scalars(select(CompanyChangeEvent)))) == 2


def test_existing_company_starts_at_first_observed_change_without_fake_creation(access_case):
    case = access_case
    with case.database.session() as session:
        company = session.scalar(select(Company).with_for_update()); before = company_snapshot(company)
        company.country = "CZ"
        event = append_company_change(session, company, case.users["ADMIN"].id, before)
        session.commit()
        assert event.action == "UPDATED" and event.version == 1
        assert event.before_snapshot["country"] is None and event.after_snapshot["country"] == "CZ"


def test_domain_write_and_history_rollback_together(access_case):
    case = access_case; identifier, _ = record(case)
    with case.database.session() as session:
        company = session.get(Company, identifier); before = company_snapshot(company); company.name = "Uncommitted change"
        append_company_change(session, company, case.users["ADMIN"].id, before)
        session.rollback()
    with case.database.session() as session:
        assert session.get(Company, identifier).name == "First audited company"
        assert len(list(session.scalars(select(CompanyChangeEvent)))) == 1


@pytest.mark.parametrize("mutation", ["update", "delete"])
def test_orm_history_cannot_be_mutated(access_case, mutation):
    case = access_case; _, identifier = record(case)
    with case.database.session() as session, pytest.raises(ImmutableAuditSnapshotError):
        event = session.get(CompanyChangeEvent, identifier)
        if mutation == "update": event.reason = "Rewrite evidence"
        else: session.delete(event)
        session.commit()


@pytest.mark.parametrize("kind", ["missing-field", "unrelated-company", "raw-extra-field", "false-creation", "remote-provenance"])
def test_invalid_snapshot_context_cannot_be_appended(access_case, kind):
    case = access_case
    with case.database.session() as session, pytest.raises(ValueError):
        company = session.scalar(select(Company)); before = company_snapshot(company); company.name = "Changed"
        kwargs = {}
        if kind == "missing-field": before.pop("name")
        if kind == "unrelated-company": before["id"] = str(uuid4())
        if kind == "raw-extra-field": before["raw"] = "unmapped synthetic property"
        if kind == "false-creation": kwargs["action"] = "CREATED"
        if kind == "remote-provenance": kwargs["source"] = {"raw": "unmapped synthetic property"}
        append_company_change(session, company, case.users["ADMIN"].id, before, **kwargs)


def test_authenticated_company_mutations_record_actor_changes_and_skip_noops(access_case):
    case = access_case
    with case.client("PRODUCTION_LEAD") as editor, case.client("ADMIN") as admin:
        result = editor.post("/api/companies", json={"name": "Created via application", "legal_name": "Synthetic legal name"})
        assert result.status_code == 201; identifier = result.json()["id"]
        path = f"/api/companies/{identifier}"
        assert editor.patch(path, json={"legal_name": None, "is_active": False}).status_code == 200
        assert editor.patch(path, json={"name": "Created via application"}).status_code == 200
        assert editor.patch(path, json={}).status_code == 200
        history = admin.get(path + "/history")
        assert history.status_code == 200 and history.headers["cache-control"] == "no-store"
        assert history.json()["next_cursor"] is None
        records = history.json()["items"]
        assert [record["version"] for record in records] == [2, 1]
        assert [record["action"] for record in records] == ["UPDATED", "CREATED"]
        assert records[1]["before"] == {} and records[0]["before"] == records[1]["after"]
        assert records[0]["after"]["legal_name"] is None and records[0]["after"]["is_active"] is False
        assert all(item["actor_id"] == str(case.users["PRODUCTION_LEAD"].id) and not item["source"] for item in records)
        assert all("password" not in item and "request_key" not in item for item in records)
        assert editor.get(path + "/history").status_code == 403


@pytest.mark.parametrize("role", [None, "PROCESSOR", "PRODUCTION_LEAD", "LEADERSHIP"])
def test_company_history_requires_current_administrator(access_case, role):
    case = access_case; identifier, _ = record(case)
    with case.client(role) as client:
        assert client.get(f"/api/companies/{identifier}/history").status_code == (401 if role is None else 403)


def test_company_history_pages_are_bounded_and_cursor_cannot_cross_companies(access_case):
    case = access_case; identifier, _ = record(case); unrelated, unrelated_event = record(case, "Other company")
    for index in range(22):
        with case.database.session() as session:
            company = session.get(Company, identifier); before = company_snapshot(company)
            company.name = f"Synthetic change {index}"
            append_company_change(session, company, case.users["ADMIN"].id, before); session.commit()
    with case.client("ADMIN") as client:
        path = f"/api/companies/{identifier}/history"
        first = client.get(path).json()
        assert first["company_id"] == str(identifier) and len(first["items"]) == 20 and first["next_cursor"]
        assert [item["version"] for item in first["items"]] == list(range(23, 3, -1))
        second = client.get(path, params={"after": first["next_cursor"]}).json()
        assert [item["version"] for item in second["items"]] == [3, 2, 1] and second["next_cursor"] is None
        assert client.get(path, params={"after": str(unrelated_event)}).status_code == 409
        assert client.get(path, params={"after": str(uuid4())}).status_code == 409
        assert client.get(path, params={"after": "invalid"}).status_code == 422
        assert client.get(f"/api/companies/{uuid4()}/history").status_code == 404
        assert len(client.get(f"/api/companies/{unrelated}/history").json()["items"]) == 1


def test_audit_failure_rolls_back_the_company_api_write(access_case, monkeypatch):
    from sqlalchemy.exc import IntegrityError
    from app.api import resources
    case = access_case
    with case.database.session() as session:
        original = session.scalar(select(Company)); identifier = original.id; before = company_snapshot(original)
    def reject(*args, **kwargs):
        args[0].flush()
        raise IntegrityError("Synthetic audit failure", {}, Exception("Synthetic failure"))
    monkeypatch.setattr(resources, "append_company_change", reject)
    with case.client("ADMIN") as client:
        assert client.post("/api/companies", json={"name": "Must roll back"}).status_code == 409
        assert client.patch(f"/api/companies/{identifier}", json={"name": "Must also roll back"}).status_code == 409
    with case.database.session() as session:
        assert len(list(session.scalars(select(Company)))) == 1
        assert company_snapshot(session.get(Company, identifier)) == before
        assert not list(session.scalars(select(CompanyChangeEvent)))


def test_conflicting_company_creation_cannot_leave_false_history(access_case):
    case = access_case; linked = str(uuid4())
    with case.client("ADMIN") as client:
        first = client.post("/api/companies", json={"name": "One owner", "notion_page_id": linked})
        assert first.status_code == 201
        assert client.post("/api/companies", json={"name": "Duplicate owner", "notion_page_id": linked}).status_code == 409
        history = client.get(f"/api/companies/{first.json()['id']}/history").json()
        assert len(history["items"]) == 1 and history["items"][0]["action"] == "CREATED"
