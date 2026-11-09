import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audit_native_registry_v24 import components, related_entity_ids, response_data
from prepare import sha


def response(tmp_path, data):
    path = tmp_path / "registry_fixture.json"
    path.write_text(json.dumps({"data": data}))
    return {"http_status": 200, "json_object_response": True, "body_file": str(path), "body_sha256": sha(path)}


def test_reporting_exception_cannot_be_misread_as_a_resolved_parent_identifier(tmp_path):
    value = response(tmp_path, {"type": "reporting-exceptions", "id": "2138001P49OLAEU33T68"})
    assert related_entity_ids({"link_type": "reporting-exception"}, value) == []
    assert related_entity_ids({"link_type": "related"}, value) == []


def test_only_explicit_lei_records_are_accepted_and_cached_body_changes_fail_closed(tmp_path):
    value = response(tmp_path, {"type": "lei-records", "id": "2138001P49OLAEU33T68"})
    assert related_entity_ids({"link_type": "related"}, value) == ["2138001P49OLAEU33T68"]
    Path(value["body_file"]).write_text("{}")
    with pytest.raises(ValueError, match="body changed"):
        response_data(value)


def test_overlap_components_follow_explicit_parent_paths_including_unselected_parents():
    groups = components(["dev", "reserved", "unrelated"], [("dev", "shared_parent"), ("reserved", "shared_parent")])
    assert sorted(groups) == [["dev", "reserved"], ["unrelated"]]
