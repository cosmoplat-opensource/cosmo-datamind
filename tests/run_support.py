"""Explicit synthetic run/artifact inputs for storage tests — DR-064 / DR-065."""
import importlib
import json

from tests.asset_support import add_data, store_at


def runs_at(root):
    # Keep RED discoverable: missing modules fail cases rather than collection.
    return importlib.import_module("domain_runs").RunStore(root)


def components():
    return {"profile_id": None, "compiler_version": "storage-test-v1",
            "rule_digest": None, "prompt_version": None, "model": {"invoked": False}}


def setup_run(tmp_path, *, session_id="chat-1"):
    root = tmp_path / "storage"
    runs = runs_at(root)
    assets = store_at(root)
    source = assets.put_source(b'original source', role="data_snapshot", media_type="text/plain")
    asset, first = add_data(assets)
    version = assets.add_version(asset["ontology_id"], assets.read_payload(asset["ontology_id"], first["version_id"]),
                                 payload_kind="datamind_ir", parent_version_id=first["version_id"],
                                 source_asset_ids=[source["source_asset_id"]])
    args = {"ontology_id": asset["ontology_id"], "version_id": version["version_id"],
            "operation": "inspect", "task": {"instruction": "离线存储测试"}, "mode": "real",
            "session_ref": {"namespace": "ont_chats", "id": session_id}, "components": components()}
    return root, assets, runs, runs.create_run(**args), args


def outputs():
    # These bytes exercise persistence only; they are not a validated robot candidate.
    return [{"name": "candidate.xml", "kind": "candidate", "media_type": "application/xml",
             "content": b'<root><BehaviorTree ID="storage-test"/></root>'},
            {"name": "validation.json", "kind": "validation", "media_type": "application/json",
             "content": json.dumps({"scope": "synthetic-storage-test"}).encode()}]


def result(status="generated"):
    return {"status": status, "execution_readiness": "not_verified",
            "parameter_sources": [{"parameter": "threshold", "value": "0.72",
                                   "reference": "urn:storage-test:profile#threshold"}],
            "issues": [] if status == "generated" else [{"code": "missing_evidence", "message": "测试缺口"}]}


def complete(runs, run):
    runs.start(run["run_id"])
    runs.finish(run["run_id"], result=result(), artifacts=outputs())
    return runs.get_run(run["run_id"])
