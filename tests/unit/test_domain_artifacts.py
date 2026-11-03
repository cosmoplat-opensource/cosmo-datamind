"""T04 RED: artifact integrity, manifest provenance and atomic completion — DR-064 / DR-065."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import sqlite3

import pytest

from tests.run_support import complete, outputs, result, runs_at, setup_run


def test_manifest_and_downloaded_bytes_match_fixed_inputs_and_outputs(tmp_path):
    root, assets, runs, run, args = setup_run(tmp_path)
    complete(runs, run)
    rid = run["run_id"]
    saved = runs_at(root)
    manifest = saved.manifest(rid)
    assert manifest["run_id"] == rid and manifest["session_ref"] == args["session_ref"]
    assert manifest["ontology_id"] == args["ontology_id"] and manifest["version_id"] == args["version_id"]
    assert manifest["ontology_snapshot"] == saved.get_run(rid)["ontology_snapshot"]
    version = assets.get_version(args["ontology_id"], args["version_id"])
    assert manifest["ontology_snapshot"]["payload_sha256"] == version["payload_sha256"]
    assert manifest["ontology_snapshot"]["source_asset_ids"] == version["source_asset_ids"]
    sources = manifest["ontology_snapshot"]["sources"]
    assert {s["source_asset_id"] for s in sources} == set(version["source_asset_ids"])
    for source in sources:
        actual = assets.get_source(source["source_asset_id"])
        for key in ("sha256", "role", "media_type"):
            assert source[key] == actual[key]
    assert manifest["task"] == args["task"] and manifest["mode"] == "real"
    assert manifest["components"] == args["components"]
    assert manifest["components"]["model"] == {"invoked": False}
    assert manifest["result"] == result()
    records = saved.artifacts(rid)
    by_name = {a["name"]: a for a in records}
    assert set(by_name) == {"candidate.xml", "validation.json", "manifest.json"}
    entries = {a["artifact_id"]: a for a in manifest["artifacts"]}
    assert set(entries) == {a["artifact_id"] for a in records if a["kind"] != "manifest"}
    for expected in outputs():
        record = by_name[expected["name"]]
        assert saved.read_artifact(rid, record["artifact_id"]) == expected["content"]
        assert record["sha256"] == hashlib.sha256(expected["content"]).hexdigest()
        for key in ("name", "kind", "media_type", "sha256"):
            assert entries[record["artifact_id"]][key] == record[key]
    raw_manifest = saved.read_artifact(rid, by_name["manifest.json"]["artifact_id"])
    assert json.loads(raw_manifest) == manifest
    assert by_name["manifest.json"]["sha256"] == hashlib.sha256(raw_manifest).hexdigest()
    assert b"api_key" not in raw_manifest and b"Authorization" not in raw_manifest


@pytest.mark.parametrize("status,lifecycle", [
    ("needs_information", "completed"), ("needs_clarification", "completed"),
    ("unsupported", "completed"), ("error", "failed"),
])
def test_business_rejection_or_error_has_report_but_no_candidate(tmp_path, status, lifecycle):
    _, _, runs, run, _ = setup_run(tmp_path)
    rid = run["run_id"]
    runs.start(rid)
    expected = result(status)
    runs.finish(rid, result=expected, artifacts=[outputs()[1]])
    row = runs.get_run(rid)
    assert row["status"] == lifecycle and row["result"] == expected
    assert {a["kind"] for a in runs.artifacts(rid)} == {"validation", "manifest"}
    assert all(a["kind"] != "candidate" for a in runs.manifest(rid)["artifacts"])
    assert runs.events(rid)[-1]["payload"]["status"] == lifecycle


@pytest.mark.parametrize("change", ["missing_candidate", "negative_with_candidate", "duplicate_name", "unknown_status"])
def test_inconsistent_completion_does_not_publish_records(tmp_path, change):
    _, _, runs, run, _ = setup_run(tmp_path)
    rid = run["run_id"]
    runs.start(rid)
    outcome, artifacts = result(), outputs()
    if change == "missing_candidate":
        artifacts = artifacts[1:]
    elif change == "negative_with_candidate":
        outcome = result("needs_information")
    elif change == "duplicate_name":
        artifacts[1]["name"] = artifacts[0]["name"]
    else:
        outcome["status"] = "invented_success"
    before, events = deepcopy(runs.get_run(rid)), deepcopy(runs.events(rid))
    with pytest.raises(ValueError):
        runs.finish(rid, result=outcome, artifacts=artifacts)
    assert runs.get_run(rid) == before and runs.events(rid) == events
    assert runs.artifacts(rid) == []
    with pytest.raises((KeyError, ValueError)):
        runs.manifest(rid)


@pytest.mark.parametrize("fault", ["write", "checksum", "transaction_commit"])
def test_partial_file_or_transaction_failure_cannot_leave_visible_success(tmp_path, monkeypatch, fault):
    root, _, runs, run, _ = setup_run(tmp_path)
    from ontology_assets.storage import Storage
    rid = run["run_id"]
    runs.start(rid)
    before, events = deepcopy(runs.get_run(rid)), deepcopy(runs.events(rid))
    with monkeypatch.context() as patch:
        if fault == "transaction_commit":
            original_transaction = Storage.transaction

            @contextmanager
            def fail_before_commit(self, *, write=False):
                with original_transaction(self, write=write) as connection:
                    yield connection
                    if write:
                        # Roll back actual SQLite changes immediately before commit.
                        raise sqlite3.OperationalError("injected commit failure")

            patch.setattr(Storage, "transaction", fail_before_commit)
        else:
            original_write = Storage.write_bytes
            calls = []

            def broken_write(self, relative, content):
                calls.append(relative)
                if fault == "write" and len(calls) == 2:
                    raise OSError("injected second file write failure")
                value = original_write(self, relative, content)
                if fault == "checksum" and len(calls) == 1:
                    (self.root / relative).write_bytes(b"corrupted after write")
                return value

            patch.setattr(Storage, "write_bytes", broken_write)
        with pytest.raises((OSError, ValueError, sqlite3.DatabaseError)):
            runs.finish(rid, result=result(), artifacts=outputs())
    reopened = runs_at(root)
    assert reopened.get_run(rid) == before
    assert reopened.events(rid) == events
    assert reopened.artifacts(rid) == []
    with pytest.raises((KeyError, ValueError)):
        reopened.manifest(rid)
    # Orphan files are allowed, but must never become visible downloads or successes.


@pytest.mark.parametrize("damage", ["changed", "missing", "symlink_escape"])
def test_artifact_damage_is_reported_without_rewriting_history(tmp_path, damage):
    root, _, runs, run, _ = setup_run(tmp_path)
    complete(runs, run)
    rid = run["run_id"]
    before = deepcopy(runs.get_run(rid))
    record = next(a for a in runs.artifacts(rid) if a["kind"] == "candidate")
    path = root / record["path"]
    if damage == "changed":
        path.write_bytes(b"modified candidate")
    elif damage == "missing":
        path.unlink()
    else:
        external = tmp_path / "outside.xml"
        external.write_bytes(path.read_bytes())  # Same digest: only confinement detects the escape.
        path.unlink()
        path.symlink_to(external)
    for read in (lambda: runs.read_artifact(rid, record["artifact_id"]), lambda: runs.manifest(rid)):
        with pytest.raises((ValueError, FileNotFoundError)):
            read()
    assert runs.get_run(rid) == before


def test_manifest_tampering_is_detected_from_registered_digest(tmp_path):
    root, _, runs, run, _ = setup_run(tmp_path)
    complete(runs, run)
    rid = run["run_id"]
    record = next(a for a in runs.artifacts(rid) if a["kind"] == "manifest")
    path = root / record["path"]
    data = json.loads(path.read_bytes())
    data["version_id"] = "another-version"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        runs.manifest(rid)


@pytest.mark.parametrize("name", ["../outside.xml", "absolute", "nested/../../outside.xml", "", "manifest.json"])
def test_artifact_names_cannot_escape_or_replace_manifest(tmp_path, name):
    _, _, runs, run, _ = setup_run(tmp_path)
    rid = run["run_id"]
    runs.start(rid)
    artifacts = outputs()
    artifacts[0]["name"] = str(tmp_path / "absolute-outside.xml") if name == "absolute" else name
    with pytest.raises(ValueError):
        runs.finish(rid, result=result(), artifacts=artifacts)
    assert runs.get_run(rid)["status"] == "running" and runs.artifacts(rid) == []
    assert not (tmp_path / "outside.xml").exists()
    assert not (tmp_path / "absolute-outside.xml").exists()


def test_downloads_require_registered_id_and_correct_run(tmp_path):
    root, _, runs, first, args = setup_run(tmp_path)
    second = runs.create_run(**args)
    complete(runs, first)
    complete(runs, second)
    record = next(a for a in runs.artifacts(first["run_id"]) if a["kind"] == "candidate")
    with pytest.raises((KeyError, ValueError)):
        runs.read_artifact(second["run_id"], record["artifact_id"])
    for value in (record["path"], str(root / record["path"]), "../outside.xml", "missing"):
        with pytest.raises((KeyError, ValueError)):
            runs.read_artifact(first["run_id"], value)
