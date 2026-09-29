import copy
import json

import pytest

from app.local_file_check import PROFILE, _parse_results
from app.local_filesystem import LocalFilesError


def response():
    return {"schema_version": 1, "results": [
        {"id": "first", "profile": PROFILE, "complete": True, "issues": [], "report": "No issues."},
        {"id": "second", "profile": PROFILE, "complete": True,
         "issues": ["PREVIEW/SPHERE_1.png: expected 1200 x 1200; actual 800 x 800"], "report": "One issue."}]}


def test_complete_results_are_bound_to_exact_requested_ids_and_restore_selection_order():
    result = _parse_results(json.dumps(response()), ["second", "first"])
    assert result[0]["issues"] and result[1]["issues"] == []
    assert result[1] == {"profile": PROFILE, "complete": True, "issues": [], "report": "No issues."}


@pytest.mark.parametrize("mutation", [
    lambda value: value.update(schema_version=True),
    lambda value: value.update(schema_version=2),
    lambda value: value.update(error_code="SOURCE_CHANGED"),
    lambda value: value["results"].pop(),
    lambda value: value["results"].append(copy.deepcopy(value["results"][0])),
    lambda value: value["results"][1].update(id="first"),
    lambda value: value["results"][1].update(id="foreign"),
    lambda value: value["results"][0].update(complete=False),
    lambda value: value["results"][0].update(complete=1),
    lambda value: value["results"][0].update(profile="BASIC_V1"),
    lambda value: value["results"][0].update(status="OK"),
    lambda value: value["results"][0].update(report=""),
    lambda value: value["results"][0].update(report="x" * 1_000_001),
    lambda value: value["results"][0].update(issues=[""]),
    lambda value: value["results"][0].update(issues=[None]),
    lambda value: value["results"][0].update(issues=["x" * 4097]),
    lambda value: value["results"][0].update(issues=["issue"] * 4097),
])
def test_partial_or_ambiguous_worker_results_cannot_be_certified(mutation):
    value = response()
    mutation(value)
    with pytest.raises(LocalFilesError, match="LOCAL_FILE_CHECK_RESULT_INVALID"):
        _parse_results(json.dumps(value), ["first", "second"])


@pytest.mark.parametrize("raw", [b"not JSON", b"\xff", b'{"schema_version":1,"schema_version":1,"results":[]}'])
def test_invalid_json_is_rejected(raw):
    with pytest.raises(LocalFilesError, match="LOCAL_FILE_CHECK_RESULT_INVALID"):
        _parse_results(raw, ["first", "second"])
