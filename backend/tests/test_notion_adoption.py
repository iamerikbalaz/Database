"""One-way synchronization permanently retires the former inbound write endpoint."""
from uuid import uuid4

import pytest

from test_application_access import access_case  # noqa: F401
from test_notion_preview import notion_case, snapshot  # noqa: F401


def test_inbound_adoption_never_reads_notion_or_changes_local_records(notion_case):
    item = notion_case
    before = snapshot(item)
    with item.case.client("ADMIN") as client:
        calls = len(item.server.requests)
        response = client.post(f"/api/companies/{item.identifier}/notion-adopt", json={"selected_fields": ["name"]})
        assert response.status_code == 410
        assert response.json()["detail"]["code"] == "NOTION_INBOUND_DISABLED"
        assert len(item.server.requests) == calls
    assert snapshot(item) == before


@pytest.mark.parametrize("role,expected", [(None, 401), ("PROCESSOR", 403), ("LEADERSHIP", 403), ("PRODUCTION_LEAD", 403), ("ADMIN", 410)])
def test_retired_route_keeps_authentication_and_current_role_checks(access_case, role, expected):
    with access_case.client(role) as client:
        assert client.post(f"/api/companies/{uuid4()}/notion-adopt", json={}).status_code == expected


def test_retired_route_keeps_csrf_and_private_receipt_reads(access_case):
    with access_case.client("ADMIN") as client:
        response = client.get(f"/api/companies/{uuid4()}/notion-adoptions/{uuid4()}")
        assert response.status_code == 404
        client.headers.pop("X-CSRF-Token")
        assert client.post(f"/api/companies/{uuid4()}/notion-adopt", json={}).status_code == 403
