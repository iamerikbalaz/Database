"""Authenticated comparison, only synthetic transport and disposable records."""
from types import SimpleNamespace
from uuid import uuid4

from pydantic import SecretStr
import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.db.models import Company, InternalUser, UserCredential, AuthSession
from app.main import create_app
from app.notion_reader import configuration_from_settings, project
from test_application_access import access_case
from test_notion_reader import Server, SOURCE, PAGE, TOKEN, schema, page


def enabled_settings(settings):
    return settings.model_copy(update=dict(notion_enabled=True, notion_access_token=SecretStr(TOKEN),
        notion_company_data_source_id=SOURCE, notion_company_properties={"name": "title", "country": "country", "website": "url%3A"}))


@pytest.fixture
def notion_case(access_case):
    case = access_case; server = Server(); settings = enabled_settings(case.app.state.settings)
    reader = server.client(configuration_from_settings(settings))
    with case.database.session() as session:
        company = session.scalar(select(Company)); company.notion_page_id = PAGE
        company.country = "CZ"; session.commit(); identifier = company.id
    case.app = create_app(settings, case.database, case.worker, notion_reader=reader)
    return SimpleNamespace(case=case, server=server, reader=reader, identifier=identifier,
        path=f"/api/companies/{identifier}/notion-preview", payload={"expected_page_id": PAGE})


def snapshot(item):
    with item.case.database.session() as session:
        company = session.get(Company, item.identifier)
        return {column.key: getattr(company, column.key) for column in Company.__table__.columns}


def test_admin_comparison_preserves_database_and_exposes_only_mapped_values(notion_case):
    item = notion_case; before = snapshot(item)
    with item.case.client("ADMIN") as client:
        status = client.get("/api/integrations/notion")
        assert status.json() == {"enabled": True, "direction": "READ_ONLY", "resource": "COMPANY", "mapped_fields": ["country", "name", "website"]}
        assert not item.server.requests
        response = client.post(item.path, json=item.payload)
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        result = response.json()
        assert result["company_id"] == str(item.identifier) and result["direction"] == "READ_ONLY"
        assert len(result["local_sha256"]) == len(result["source"]["observation_sha256"]) == 64
        assert result["source"]["page_id"] == PAGE and result["source"]["data_source_id"] == SOURCE
        assert result["fields"] == [
            {"field": "country", "current": "CZ", "observed": None, "changed": True},
            {"field": "name", "current": "Synthetic company", "observed": "Synthetic česká company", "changed": True},
            {"field": "website", "current": None, "observed": "https://example.invalid/", "changed": True}]
        assert "SYNTHETIC-PRIVATE-CONTENT" not in response.text and TOKEN not in response.text
        assert "properties" not in response.text and "values" not in result["source"]
        repeated = client.post(item.path, json=item.payload)
        assert repeated.json() == result
    assert snapshot(item) == before


@pytest.mark.parametrize("role", [None, "PRODUCTION_LEAD", "LEADERSHIP", "PROCESSOR"])
def test_comparison_requires_current_admin_and_never_calls_notion_for_other_roles(notion_case, role):
    item = notion_case
    with item.case.client(role) as client:
        expected = 401 if role is None else 403
        assert client.get("/api/integrations/notion").status_code == expected
        assert client.post(item.path, json=item.payload).status_code == expected
    assert not item.server.requests


def test_comparison_requires_csrf_and_rejects_unconfigured_request_fields(notion_case):
    item = notion_case
    with item.case.client("ADMIN") as client:
        token = client.headers.pop("X-CSRF-Token")
        assert client.post(item.path, json=item.payload).status_code == 403
        client.headers["X-CSRF-Token"] = token
        assert client.post(item.path, json=item.payload, headers={"Origin": "https://wrong.invalid"}).status_code == 403
        assert client.post(item.path, json=item.payload | {"source_id": SOURCE}).status_code == 422
    assert not item.server.requests


@pytest.mark.parametrize("selection,status", [(None, 409), ("not-an-id", 409), ("different", 409), ("missing", 404), ("bad-input", 422)])
def test_comparison_requires_existing_exact_explicit_link(notion_case, selection, status):
    item = notion_case
    path = item.path; payload = item.payload
    if selection in {None, "not-an-id"}:
        with item.case.database.session() as session:
            session.get(Company, item.identifier).notion_page_id = selection; session.commit()
    elif selection == "different": payload = {"expected_page_id": str(uuid4())}
    elif selection == "missing": path = f"/api/companies/{uuid4()}/notion-preview"
    else: payload = {"expected_page_id": "x" * 36}
    with item.case.client("ADMIN") as client: assert client.post(path, json=payload).status_code == status
    assert not item.server.requests


@pytest.mark.parametrize("disabled", [True, False])
def test_disabled_or_mismatched_reader_never_performs_http(notion_case, disabled):
    item = notion_case; case = item.case
    settings = case.app.state.settings.model_copy(update={"notion_enabled": False}) if disabled else case.app.state.settings
    if not disabled: item.reader.configuration = item.reader.configuration.model_copy(update={"data_source_id": str(uuid4())})
    case.app = create_app(settings, case.database, case.worker, notion_reader=item.reader)
    with case.client("ADMIN") as client:
        result = client.post(item.path, json=item.payload)
        assert result.status_code == 503
        assert result.json()["detail"]["code"] == ("NOTION_DISABLED" if disabled else "NOTION_CONFIGURATION_INVALID")
    assert not item.server.requests


@pytest.mark.parametrize("change,status", [("name", 409), ("link", 409), ("active", 409),
    ("role", 403), ("disable-user", 401), ("password", 403), ("revoke", 401)])
@pytest.mark.parametrize("remote_error", [False, True])
def test_comparison_rechecks_local_context_and_actor_after_remote_response(notion_case, change, status, remote_error):
    item = notion_case; case = item.case
    async def hook(request):
        with case.database.session() as session:
            company = session.get(Company, item.identifier); user = session.get(InternalUser, case.users["ADMIN"].id)
            if change == "name": company.name = "New local name"
            elif change == "link": company.notion_page_id = str(uuid4())
            elif change == "active": company.is_active = False
            elif change == "role": user.role = "PROCESSOR"
            elif change == "disable-user": user.is_active = False
            elif change == "password": session.get(UserCredential, user.id).must_change_password = True
            else:
                for stored in session.scalars(select(AuthSession).where(AuthSession.user_id == user.id)): stored.revoked_at = stored.created_at
            session.commit()
        return item.server.response(schema(), status=503 if remote_error else 200)
    item.server.hook = hook
    with case.client("ADMIN") as client:
        result = client.post(item.path, json=item.payload)
        assert result.status_code == status
        assert "Synthetic česká company" not in result.text
    assert len(item.server.requests) == 1


@pytest.mark.parametrize("kind", ["page", "source", "mapping", "digest", "control", "exception"])
def test_adapter_results_are_revalidated_before_api_disclosure(notion_case, kind):
    from app.notion_reader import _hash
    item = notion_case
    original = project(item.reader.configuration, PAGE, schema(), page())
    async def forged(*args, **kwargs):
        if kind == "exception": raise RuntimeError("SYNTHETIC-PRIVATE-CONTENT")
        data = original.model_dump(mode="python")
        data["values"] = original.values
        if kind == "control":
            data["values"] = tuple(value.model_copy(update={"value": "unsafe\x00value"}) if value.field == "name" else value for value in data["values"])
        else: data[{"page": "page_id", "source": "data_source_id", "mapping": "mapping_sha256", "digest": "observation_sha256"}[kind]] = str(uuid4()) if kind in {"page", "source"} else "a" * 64
        observation = original.model_construct(**data)
        if kind != "digest": observation = observation.model_copy(update={"observation_sha256": _hash(observation.model_dump(mode="json", exclude={"observation_sha256"}))})
        return observation
    item.reader.company = forged
    with item.case.client("ADMIN") as client:
        response = client.post(item.path, json=item.payload)
        assert response.status_code == 503 and response.json()["detail"]["code"] == "NOTION_RESPONSE_INVALID"
        assert "SYNTHETIC-PRIVATE-CONTENT" not in response.text and "unsafe" not in response.text


def test_rate_limit_is_bounded_and_propagated_without_replay(notion_case):
    item = notion_case
    async def limit(request): return item.server.response(status=529, headers={"Retry-After": "120"})
    item.server.hook = limit
    with item.case.client("ADMIN") as client:
        for _ in range(2):
            response = client.post(item.path, json=item.payload)
            assert response.status_code == 503 and response.json()["detail"]["code"] == "NOTION_RATE_LIMITED"
            assert 1 <= int(response.headers["Retry-After"]) <= 120
    assert len(item.server.requests) == 1


@pytest.mark.parametrize("change", [{"notion_access_token": None}, {"notion_company_data_source_id": "invalid"},
    {"notion_company_properties": {}}, {"notion_company_properties": {"name": "title", "country": "title"}},
    {"notion_company_properties": {"name": "title", "role": "role"}}])
def test_enabled_settings_require_explicit_safe_configuration(change):
    settings = enabled_settings(Settings(_env_file=None)).model_dump() | change
    with pytest.raises(ValueError, match="Notion requires") as error: Settings(_env_file=None, **settings)
    assert TOKEN not in str(error.value)


def test_default_notion_config_is_disabled_and_environment_mapping_is_explicit(monkeypatch):
    assert not Settings(_env_file=None).notion_enabled
    monkeypatch.setenv("NOTION_ENABLED", "true"); monkeypatch.setenv("NOTION_ACCESS_TOKEN", TOKEN)
    monkeypatch.setenv("NOTION_COMPANY_DATA_SOURCE_ID", SOURCE)
    monkeypatch.setenv("NOTION_COMPANY_PROPERTIES", '{"name":"title"}')
    configured = Settings(_env_file=None)
    assert [prop.field for prop in configuration_from_settings(configured).properties] == ["name"]
    assert TOKEN not in repr(configured)
