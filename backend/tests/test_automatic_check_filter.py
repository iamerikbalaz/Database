"""Automatic file-check filters compose with normal scope and archive filters."""
import pytest

from app.db.models import PBRMaterial
from test_application_access import access_case
from test_material_archives import apply, prepare


@pytest.mark.parametrize("status", ["NOT_CHECKED", "OK", "ISSUES"])
def test_automatic_filter_preserves_access_and_intersects_color_and_search(access_case, status):
    case = access_case
    own, other = case.materials
    with case.database.session() as session:
        for item in (own, other):
            material = session.get(PBRMaterial, item.id)
            material.automatic_file_check_status = status
            material.metadata_state.hex_color = "#FFFFFF" if item.id == own.id else "#FF822D"
            material.note = "#review"
        session.commit()
    query = {"automatic_file_check_status": status}
    with case.client("PROCESSOR") as client:
        assert [item["id"] for item in client.get("/api/materials", params=query).json()] == [str(own.id)]
    with case.client("ADMIN") as client:
        assert len(client.get("/api/materials", params=query).json()) == 2
        matching = {**query, "color_hex": "#FFFFFF", "search": "#review"}
        assert [item["id"] for item in client.get("/api/materials", params=matching).json()] == [str(own.id)]
        assert client.get("/api/materials", params={**matching, "search": "#missing"}).json() == []
        different = "OK" if status != "OK" else "ISSUES"
        assert client.get("/api/materials", params={"automatic_file_check_status": different}).json() == []
        assert len(client.get("/api/materials").json()) == 2


def test_automatic_filter_works_for_archived_materials(access_case):
    case = access_case
    identifier = case.materials[0].id
    with case.client("ADMIN") as client:
        assert apply(client, identifier, prepare(client, identifier)).status_code == 200
        with case.database.session() as session:
            session.get(PBRMaterial, identifier).automatic_file_check_status = "ISSUES"
            session.commit()
        query = {"is_archived": True, "automatic_file_check_status": "ISSUES"}
        result = client.get("/api/materials", params=query)
        assert result.status_code == 200
        assert [item["id"] for item in result.json()] == [str(identifier)]
        assert client.get("/api/materials", params={**query, "automatic_file_check_status": "OK"}).json() == []
        assert client.get("/api/materials", params={"automatic_file_check_status": "ISSUES"}).json() == []


@pytest.mark.parametrize("status", ["issues", "VALID", "", "OK' OR 1=1"])
def test_automatic_filter_rejects_unknown_status(access_case, status):
    with access_case.client("ADMIN") as client:
        assert client.get("/api/materials", params={"automatic_file_check_status": status}).status_code == 422
