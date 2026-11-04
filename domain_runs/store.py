"""Transactionally persist run state, events and artifacts — DR-064 / DR-065."""
from datetime import datetime, timezone
import json
import uuid

from ontology_assets.payloads import json_text
from ontology_assets.storage import Storage, digest
from ontology_assets.store import AssetStore, _required, _version

from . import build_contracts, contracts

JSON_FIELDS = ("task", "session_ref", "components", "ontology_snapshot", "result", "build_input", "output_asset_ref")
MANIFEST_FIELDS = ("run_id", "ontology_id", "version_id", "operation", "task", "mode", "session_ref",
                   "components", "ontology_snapshot", "status", "result", "retry_of", "created_at", "updated_at")
ARTIFACT_FIELDS = ("artifact_id", "name", "kind", "media_type", "sha256", "byte_size")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _decode(row):
    record = dict(row)
    for key in JSON_FIELDS:
        record[key] = json.loads(record[key]) if record[key] is not None else None
    return record


def _run(conn, run_id):
    row = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if row is None:
        raise KeyError("运行记录不存在。")
    return _decode(row)


def _expect(run, *states):
    if run["status"] not in states:
        raise ValueError("运行状态不允许此操作。")


def _manifest(run, artifacts):
    data = {field: run[field] for field in MANIFEST_FIELDS}
    # Keep every schema-2 manifest byte/shape valid; new fields apply only to builds.
    if run["build_input"] is not None:
        data.update(build_input=run["build_input"], output_asset_ref=run["output_asset_ref"])
    data["artifacts"] = [{field: item[field] for field in ARTIFACT_FIELDS}
                         for item in artifacts if item["kind"] != "manifest"]
    return data


class RunStore:
    def __init__(self, root, *, read_only=False):
        # Schema initialization never recovers or modifies active run state.
        self.storage = Storage(root, read_only=read_only)

    def _snapshot(self, conn, ontology_id, version_id):
        asset = _required(conn, "SELECT * FROM assets WHERE ontology_id=?", (ontology_id,))
        version = _version(_required(conn, "SELECT * FROM versions WHERE ontology_id=? AND version_id=?",
                                    (ontology_id, version_id)))
        self.storage.read_bytes(version["path"], version["payload_sha256"])
        sources = []
        for sid in version["source_asset_ids"]:
            source = _required(conn, "SELECT * FROM sources WHERE source_asset_id=?", (sid,))
            self.storage.read_bytes(source["path"], source["sha256"])
            sources.append({key: source[key] for key in ("source_asset_id", "role", "media_type", "sha256", "byte_size")})
        validation = version["validation"] or {}
        return {"ontology_id": ontology_id, "version_id": version_id, "domain": asset["domain"],
                "payload_kind": version["payload_kind"], "payload_sha256": version["payload_sha256"],
                "source_asset_ids": version["source_asset_ids"], "sources": sources,
                "profile_id": version["metadata"].get("profile_id"),
                "artifact_contract": validation.get("artifact_contract"),
                "rule_digest": validation.get("validation_shapes_sha256")}

    def _verify_snapshot(self, conn, run):
        if run["build_input"] is not None:
            if (run["operation"] != "build_ontology" or run["mode"] != "real"
                    or any(run[key] is not None for key in ("ontology_id", "version_id", "ontology_snapshot"))):
                raise ValueError("构建运行输入类型不一致。")
            build_contracts.verify_input(self.storage, conn, run["build_input"])
            return
        current = self._snapshot(conn, run["ontology_id"], run["version_id"])
        fixed = run["ontology_snapshot"]
        # Publication can add validation metadata; it cannot alter pinned payloads/sources.
        for key in ("ontology_id", "version_id", "domain", "payload_kind", "payload_sha256",
                    "source_asset_ids", "sources", "profile_id"):
            if current[key] != fixed[key]:
                raise ValueError("本体或来源与运行固定快照不一致。")

    def _event(self, conn, run_id, kind, payload):
        seq = conn.execute("SELECT COALESCE(MAX(seq),0)+1 FROM run_events WHERE run_id=?", (run_id,)).fetchone()[0]
        record = {"run_id": run_id, "seq": seq, "kind": kind, "payload": json_text(payload), "created_at": _now()}
        conn.execute("INSERT INTO run_events VALUES (:run_id,:seq,:kind,:payload,:created_at)", record)
        return {**record, "payload": json.loads(record["payload"])}

    def _insert(self, conn, inputs, snapshot, retry_of=None):
        timestamp = _now()
        record = {"build_input": None, "output_asset_ref": None, **inputs, "ontology_snapshot": snapshot, "run_id": "run_" + uuid.uuid4().hex,
                  "status": "queued", "result": None, "retry_of": retry_of,
                  "created_at": timestamp, "updated_at": timestamp}
        stored = {**record}
        for field in JSON_FIELDS:
            stored[field] = json_text(stored[field]) if stored[field] is not None else None
        conn.execute("""INSERT INTO runs VALUES (:run_id,:ontology_id,:version_id,:operation,:task,:mode,
            :session_ref,:components,:ontology_snapshot,:status,:result,:retry_of,:created_at,:updated_at,
            :build_input,:output_asset_ref)""", stored)
        self._event(conn, record["run_id"], "state", {"status": "queued"})
        return _decode(stored)

    def create_run(self, *, ontology_id, version_id, operation, task, mode, session_ref, components):
        contracts.text(ontology_id, "ontology_id")
        contracts.text(version_id, "version_id")
        contracts.text(operation, "operation")
        if operation == "build_ontology":
            raise ValueError("本体构建必须通过来源输入型运行创建。")
        if mode not in ("real", "demo"):
            raise ValueError("运行模式必须为 real 或 demo。")
        inputs = {"ontology_id": ontology_id, "version_id": version_id, "operation": operation,
                  "task": contracts.object_copy(task, "task"), "mode": mode,
                  "session_ref": contracts.session(session_ref), "components": contracts.components(components)}
        with self.storage.transaction(write=True) as conn:
            snapshot = self._snapshot(conn, ontology_id, version_id)
            return self._insert(conn, inputs, snapshot)

    def create_build_run(self, *, name, recipe, profile_id, source_asset_ids, session_ref, components):
        inputs = {"ontology_id": None, "version_id": None, "operation": "build_ontology", "task": {},
                  "mode": "real", "session_ref": contracts.session(session_ref),
                  "components": contracts.components(components)}
        if inputs["components"]["profile_id"] != profile_id or inputs["components"]["model"]["invoked"]:
            raise ValueError("构建组件必须匹配 profile，且不调用模型。")
        with self.storage.transaction(write=True) as conn:
            inputs["build_input"] = build_contracts.build_input(self.storage, conn, name=name, recipe=recipe,
                profile_id=profile_id, source_asset_ids=source_asset_ids)
            return self._insert(conn, inputs, None)

    def get_run(self, run_id):
        with self.storage.transaction() as conn:
            return _run(conn, run_id)

    def list_runs(self, *, session_ref=None):
        clause, params = "", ()
        if session_ref is not None:
            clause, params = " WHERE session_ref=?", (json_text(contracts.session(session_ref)),)
        with self.storage.transaction() as conn:
            return [_decode(row) for row in conn.execute("SELECT * FROM runs" + clause + " ORDER BY rowid", params)]

    def _state(self, conn, run_id, status):
        conn.execute("UPDATE runs SET status=?, updated_at=? WHERE run_id=?", (status, _now(), run_id))
        self._event(conn, run_id, "state", {"status": status})

    def start(self, run_id):
        with self.storage.transaction(write=True) as conn:
            run = _run(conn, run_id)
            _expect(run, "queued")
            self._verify_snapshot(conn, run)
            self._state(conn, run_id, "running")
            return _run(conn, run_id)

    def append_event(self, run_id, *, kind, payload):
        contracts.text(kind, "kind")
        if kind == "state":
            raise ValueError("状态事件只能由运行状态转换产生。")
        payload = contracts.object_copy(payload, "payload")
        with self.storage.transaction(write=True) as conn:
            _expect(_run(conn, run_id), "running")
            return self._event(conn, run_id, kind, payload)

    def events(self, run_id, *, after=0):
        if type(after) is not int or after < 0:
            raise ValueError("事件游标必须是非负整数。")
        with self.storage.transaction() as conn:
            _run(conn, run_id)
            return [{**dict(row), "payload": json.loads(row["payload"])} for row in conn.execute(
                "SELECT * FROM run_events WHERE run_id=? AND seq>? ORDER BY seq", (run_id, after))]

    def recover_interrupted(self):
        """Call once from a single host's startup, before accepting new work (DR-064)."""
        with self.storage.transaction(write=True) as conn:
            ids = [row[0] for row in conn.execute("SELECT run_id FROM runs WHERE status='running' ORDER BY rowid")]
            for run_id in ids:
                self._state(conn, run_id, "interrupted")
            return ids

    def retry(self, run_id):
        with self.storage.transaction(write=True) as conn:
            old = _run(conn, run_id)
            _expect(old, "interrupted", "failed")
            self._verify_snapshot(conn, old)
            inputs = {key: old[key] for key in ("ontology_id", "version_id", "operation", "task", "mode",
                                               "session_ref", "components", "build_input")}
            return self._insert(conn, inputs, old["ontology_snapshot"], retry_of=run_id)

    def _write_artifact(self, run_id, artifact):
        aid = "art_" + uuid.uuid4().hex
        relative = f"artifacts/{run_id}/{aid}/{artifact['name']}"
        content = artifact["content"]
        record = {"artifact_id": aid, "run_id": run_id, "name": artifact["name"], "kind": artifact["kind"],
                  "media_type": artifact["media_type"], "sha256": digest(content), "byte_size": len(content),
                  "path": relative, "created_at": _now()}
        self.storage.write_bytes(relative, content)
        return record

    def finish(self, run_id, *, result, artifacts):
        outcome, artifacts = contracts.completion(result, artifacts)
        with self.storage.transaction(write=True) as conn:
            run = _run(conn, run_id)
            _expect(run, "running")
            self._verify_snapshot(conn, run)
            if run["build_input"] is not None:
                raise ValueError("来源构建不能通过生成完成契约提交。")
            return self._complete(conn, run, outcome, artifacts)

    def _complete(self, conn, run, outcome, artifacts):
        """One shared durable commit path; caller owns the existing transaction."""
        run_id = run["run_id"]
        run.update(status="failed" if outcome["status"] == "error" else "completed",
                   result=outcome, updated_at=_now())
        records = [self._write_artifact(run_id, artifact) for artifact in artifacts]
        manifest = _manifest(run, records)
        records.append(self._write_artifact(run_id, {"name": "manifest.json", "kind": "manifest",
                       "media_type": "application/json", "content": json_text(manifest).encode("utf-8")}))
        for record in records:
            self.storage.read_bytes(record["path"], record["sha256"])
            conn.execute("""INSERT INTO run_artifacts VALUES (:artifact_id,:run_id,:name,:kind,:media_type,
                :sha256,:byte_size,:path,:created_at)""", record)
        # Recheck sources immediately before exposing success, including post-write damage.
        if outcome["status"] != "error" or run["build_input"] is None:
            self._verify_snapshot(conn, run)
        conn.execute("UPDATE runs SET status=?,result=?,updated_at=?,output_asset_ref=? WHERE run_id=?",
                     (run["status"], json_text(outcome), run["updated_at"],
                      json_text(run["output_asset_ref"]) if run["output_asset_ref"] is not None else None, run_id))
        self._event(conn, run_id, "state", {"status": run["status"]})
        return run

    def finish_build(self, run_id, *, result, ontology):
        # Validate expensive RDF outside the SQLite write lock (same ordering as publication).
        initial = self.get_run(run_id)
        _expect(initial, "running")
        if initial["build_input"] is None or initial["operation"] != "build_ontology":
            raise ValueError("只有来源构建运行可以提交本体候选。")
        outcome = build_contracts.completion(result, ontology, initial["build_input"])
        assets = AssetStore(self.storage.root)
        with self.storage.transaction(write=True) as conn:
            run = _run(conn, run_id)
            _expect(run, "running")
            if run["build_input"] != initial["build_input"]:
                raise ValueError("提交期间构建输入发生变化。")
            artifacts = []
            if outcome["status"] == "built":
                self._verify_snapshot(conn, run)
                fixed = run["build_input"]
                asset = assets._create_asset(conn, fixed["name"], "robotics")
                version = assets._add_version(conn, asset["ontology_id"], ontology, payload_kind="robotics_rdf",
                    source_asset_ids=[s["source_asset_id"] for s in fixed["sources"]],
                    metadata={"profile_id": fixed["profile_id"], "build_recipe": fixed["recipe"], "build_run_id": run_id})
                run["output_asset_ref"] = {key: version[key] for key in ("ontology_id", "version_id")}
                artifacts.append({"name": "candidate.owl", "kind": "ontology", "media_type": "application/rdf+xml",
                                  "content": ontology})
            artifacts.append({"name": "validation.json", "kind": "validation", "media_type": "application/json",
                              "content": json_text(outcome).encode("utf-8")})
            return self._complete(conn, run, outcome, artifacts)

    def artifacts(self, run_id):
        with self.storage.transaction() as conn:
            _run(conn, run_id)
            return [dict(row) for row in conn.execute("SELECT * FROM run_artifacts WHERE run_id=? ORDER BY rowid",
                                                     (run_id,))]

    def read_artifact(self, run_id, artifact_id):
        with self.storage.transaction() as conn:
            _run(conn, run_id)
            record = _required(conn, "SELECT * FROM run_artifacts WHERE run_id=? AND artifact_id=?",
                               (run_id, artifact_id))
            return self.storage.read_bytes(record["path"], record["sha256"])

    def manifest(self, run_id):
        with self.storage.transaction() as conn:
            run = _run(conn, run_id)
            _expect(run, "completed", "failed")
            records = [dict(row) for row in conn.execute("SELECT * FROM run_artifacts WHERE run_id=? ORDER BY rowid",
                                                         (run_id,))]
            manifests = [item for item in records if item["kind"] == "manifest"]
            if len(manifests) != 1:
                raise ValueError("运行缺少唯一的产物清单。")
            record = manifests[0]
            value = json.loads(self.storage.read_bytes(record["path"], record["sha256"]))
            if value != _manifest(run, records):
                raise ValueError("清单与登记的运行或工件不一致。")
            for item in records:
                self.storage.read_bytes(item["path"], item["sha256"])
            return value
