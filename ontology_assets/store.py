"""Asset/version services with explicit publication and consumption — DR-059 / DR-064 / DR-065."""
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

from srv_context import confine, _RDF_LOCK

from .payloads import KINDS, data_ir, json_text, validate_payload
from .storage import Storage, digest


def _now():
    return datetime.now(timezone.utc).isoformat()


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 必须是非空字符串。")
    return value


def _required(conn, sql, params):
    row = conn.execute(sql, params).fetchone()
    if row is None:
        raise KeyError("指定的资产、版本或来源不存在。")
    return dict(row)


def _version(row):
    result = dict(row)
    for field in ("source_asset_ids", "metadata", "validation"):
        result[field] = json.loads(result[field]) if result[field] is not None else None
    return result


class AssetStore:
    def __init__(self, root, *, read_only=False):
        self.storage = Storage(root, read_only=read_only)

    def _create_asset(self, conn, name, domain):
        _text(name, "name")
        if not isinstance(domain, str) or domain not in KINDS:
            raise ValueError("本体领域必须是 data 或 robotics。")
        record = {"ontology_id": "ont_" + uuid.uuid4().hex, "name": name,
                  "domain": domain, "created_at": _now()}
        conn.execute("INSERT INTO assets VALUES (:ontology_id,:name,:domain,:created_at)", record)
        return record

    def create_asset(self, *, name, domain):
        with self.storage.transaction(write=True) as conn:
            return self._create_asset(conn, name, domain)

    def list_assets(self):
        with self.storage.transaction() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM assets ORDER BY rowid")]

    def get_asset(self, ontology_id):
        with self.storage.transaction() as conn:
            return _required(conn, "SELECT * FROM assets WHERE ontology_id=?", (ontology_id,))

    def put_source(self, content, *, role, media_type):
        with self.storage.transaction(write=True) as conn:
            return self._put_source(conn, content, role=role, media_type=media_type)

    def _put_source(self, conn, content, *, role, media_type):
        """DR-064: allow source registration inside an admission transaction."""
        if not isinstance(content, bytes):
            raise ValueError("来源必须为 bytes。")
        _text(role, "role")
        _text(media_type, "media_type")
        sid = "src_" + uuid.uuid4().hex
        relative = f"sources/{sid}.bin"
        record = {"source_asset_id": sid, "role": role, "media_type": media_type,
                  "sha256": digest(content), "byte_size": len(content), "path": relative, "created_at": _now()}
        self.storage.write_bytes(relative, content)
        conn.execute("INSERT INTO sources VALUES (:source_asset_id,:role,:media_type,:sha256,"
                     ":byte_size,:path,:created_at)", record)
        return record

    def get_source(self, source_asset_id):
        with self.storage.transaction() as conn:
            return _required(conn, "SELECT * FROM sources WHERE source_asset_id=?", (source_asset_id,))

    def read_source(self, source_asset_id):
        record = self.get_source(source_asset_id)
        return self.storage.read_bytes(record["path"], record["sha256"])

    def _add_version(self, conn, ontology_id, content, *, payload_kind, parent_version_id=None,
                     source_asset_ids=None, metadata=None):
        asset = _required(conn, "SELECT * FROM assets WHERE ontology_id=?", (ontology_id,))
        validate_payload(asset["domain"], payload_kind, content)
        if parent_version_id is not None:
            _required(conn, "SELECT version_id FROM versions WHERE ontology_id=? AND version_id=?",
                      (ontology_id, parent_version_id))
        sources = [] if source_asset_ids is None else source_asset_ids
        if not isinstance(sources, list) or any(not isinstance(s, str) for s in sources):
            raise ValueError("source_asset_ids 必须是来源 ID 列表。")
        for sid in sources:
            source = _required(conn, "SELECT * FROM sources WHERE source_asset_id=?", (sid,))
            self.storage.read_bytes(source["path"], source["sha256"])
        metadata = {} if metadata is None else metadata
        if not isinstance(metadata, dict):
            raise ValueError("metadata 必须是对象。")
        vid = "ver_" + uuid.uuid4().hex
        extension = "json" if payload_kind == "datamind_ir" else "owl"
        relative = f"versions/{vid}.{extension}"
        record = {"version_id": vid, "ontology_id": ontology_id, "parent_version_id": parent_version_id,
                  "payload_kind": payload_kind, "payload_sha256": digest(content), "byte_size": len(content),
                  "path": relative, "source_asset_ids": json_text(sources), "metadata": json_text(metadata),
                  "publication_state": "candidate", "publication_mode": None, "validation": None,
                  "created_at": _now()}
        self.storage.write_bytes(relative, content)
        conn.execute("""INSERT INTO versions VALUES (
            :version_id,:ontology_id,:parent_version_id,:payload_kind,:payload_sha256,:byte_size,:path,
            :source_asset_ids,:metadata,:publication_state,:publication_mode,:validation,:created_at)""", record)
        return _version(record)

    def add_version(self, ontology_id, content, *, payload_kind, parent_version_id=None,
                    source_asset_ids=None, metadata=None):
        with self.storage.transaction(write=True) as conn:
            return self._add_version(conn, ontology_id, content, payload_kind=payload_kind,
                                     parent_version_id=parent_version_id, source_asset_ids=source_asset_ids,
                                     metadata=metadata)

    def list_versions(self, ontology_id):
        with self.storage.transaction() as conn:
            _required(conn, "SELECT ontology_id FROM assets WHERE ontology_id=?", (ontology_id,))
            return [_version(row) for row in conn.execute(
                "SELECT * FROM versions WHERE ontology_id=? ORDER BY rowid", (ontology_id,))]

    def get_version(self, ontology_id, version_id):
        with self.storage.transaction() as conn:
            return _version(_required(conn, "SELECT * FROM versions WHERE ontology_id=? AND version_id=?",
                                      (ontology_id, version_id)))

    def _version_number(self, conn, ontology_id, version_id):
        """DR-067: asset-local ordinal in the append-only registration order."""
        row = _required(conn, 'SELECT rowid AS position FROM versions WHERE ontology_id=? AND version_id=?',
                        (ontology_id, version_id))
        return conn.execute('SELECT COUNT(*) FROM versions WHERE ontology_id=? AND rowid<=?',
                            (ontology_id, row['position'])).fetchone()[0]

    def version_number(self, ontology_id, version_id):
        with self.storage.transaction() as conn:
            return self._version_number(conn, ontology_id, version_id)

    def read_payload(self, ontology_id, version_id):
        record = self.get_version(ontology_id, version_id)
        return self.storage.read_bytes(record["path"], record["payload_sha256"])

    def load_data_ir(self, ontology_id, version_id):
        record = self.get_version(ontology_id, version_id)
        if record["payload_kind"] != "datamind_ir":
            raise ValueError("指定版本不是数据 IR，不能用于数据本体读取。")
        return data_ir(self.read_payload(ontology_id, version_id))

    def _robotics_context(self, record, mode):
        if record["payload_kind"] != "robotics_rdf":
            raise ValueError("指定版本不是机器人本体。")
        from domains.robotics.consumer import load_ontology
        # DR-064: use the same lock as server SPARQL/SHACL, not a per-store lock.
        with _RDF_LOCK:
            self.storage.read_bytes(record["path"], record["payload_sha256"])
            context = load_ontology(Path(confine(self.storage.root, record["path"])), mode=mode)
        if record['metadata'].get('evidence_policy'):
            from domains.robotics.evidence import policy, POLICY
            if policy(context['graph']) != POLICY:
                raise ValueError('治理版本不可退回旧证据契约。')
        if context["validation"]["ontology_sha256"] != record["payload_sha256"]:
            raise ValueError("校验读取的本体与固定版本摘要不一致。")
        return context

    def publish_version(self, ontology_id, version_id, *, mode="real"):
        record = self.get_version(ontology_id, version_id)
        context = self._robotics_context(record, mode)
        with self.storage.transaction(write=True) as conn:
            current = _required(conn, "SELECT * FROM versions WHERE ontology_id=? AND version_id=?",
                                (ontology_id, version_id))
            self.storage.read_bytes(current["path"], current["payload_sha256"])
            if current["publication_state"] == "published":
                if current["publication_mode"] != mode:
                    raise ValueError("不能改变已发布版本的模式。")
            else:
                conn.execute("UPDATE versions SET publication_state='published', publication_mode=?, validation=? "
                             "WHERE ontology_id=? AND version_id=?",
                             (mode, json_text(context["validation"]), ontology_id, version_id))
        return self.get_version(ontology_id, version_id)

    def load_robotics(self, ontology_id, version_id, *, mode="real"):
        record = self.get_version(ontology_id, version_id)
        if record["publication_state"] != "published" or record["publication_mode"] != mode:
            raise ValueError("只能加载已发布且模式一致的机器人版本。")
        return self._robotics_context(record, mode)

    def load_robotics_application(self, ontology_id, version_id, *, mode="real"):
        """DR-066: candidate application still uses the complete consumer validation."""
        return self._robotics_context(self.get_version(ontology_id, version_id), mode)

    def _register_legacy(self, entries):
        """Commit each prepared inventory as one transaction; callbacks have no DB lock."""
        results = []
        with self.storage.transaction(write=True) as conn:
            for entry in entries:
                key, name, content, metadata = entry
                fingerprint = digest(content + b"\0" + json_text(metadata).encode("utf-8"))
                binding = conn.execute("SELECT * FROM legacy_assets WHERE legacy_key=?", (key,)).fetchone()
                if binding is not None and binding["fingerprint"] == fingerprint:
                    stored = _required(conn, "SELECT * FROM versions WHERE ontology_id=? AND version_id=?",
                                       (binding["ontology_id"], binding["version_id"]))
                    self.storage.read_bytes(stored["path"], stored["payload_sha256"])
                    aid, vid = binding["ontology_id"], binding["version_id"]
                else:
                    aid = binding["ontology_id"] if binding else self._create_asset(conn, name, "data")["ontology_id"]
                    version = self._add_version(conn, aid, content, payload_kind="datamind_ir",
                                                parent_version_id=binding["version_id"] if binding else None,
                                                metadata=metadata)
                    vid = version["version_id"]
                    conn.execute("INSERT INTO legacy_assets VALUES (?,?,?,?) ON CONFLICT(legacy_key) "
                                 "DO UPDATE SET version_id=excluded.version_id, fingerprint=excluded.fingerprint",
                                 (key, aid, vid, fingerprint))
                results.append({"legacy_key": key, "ontology_id": aid, "version_id": vid})
        return results
