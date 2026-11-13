"""Self-contained versioned application JSON/OWL and atomic completion updates — DR-072."""
from copy import deepcopy
import base64
import json

from rdflib import Graph
from domains.robotics._application_bundle import CONTRACT, _current, restore_bundle
from domains.robotics.modeling.common import digest as json_digest
from ontology_assets.payloads import json_text
from ontology_assets.store import AssetStore, _version, _required
from ontology_assets.storage import digest
from srv_context import _RDF_LOCK


def is_application(version):
    # A declared unknown version must reach the rejecting reader, never legacy fallback.
    return version['metadata'].get('application_contract') is not None


def load(storage, conn, version):
    if version['metadata'].get('application_contract')!=CONTRACT or version['payload_kind']!='robotics_rdf':
        raise ValueError('应用工件版本不受支持。')
    row = _required(conn, 'SELECT * FROM robotics_application_documents WHERE version_id=?', (version['version_id'],))
    encoded = storage.read_bytes(row['path'], row['sha256'])
    raw = storage.read_bytes(version['path'], version['payload_sha256'])
    doc = json.loads(encoded)
    if (restore_bundle(encoded) != raw or doc['output_asset_ref'] != {k: version[k] for k in ('ontology_id','version_id')}
            or doc['parent_version_id'] != version['parent_version_id']):
        raise ValueError('应用 JSON 与固定版本不一致。')
    run_id=version['metadata'].get('build_run_id')
    if run_id:
        from domain_runs.store import _run, _manifest
        run=_run(conn,run_id)
        if run['operation']!='adapt_execution' or run['status']!='completed' or run['output_asset_ref']!=doc['output_asset_ref']:
            raise ValueError('应用工件与适配运行不一致。')
        records=[dict(r) for r in conn.execute('SELECT * FROM run_artifacts WHERE run_id=? ORDER BY rowid',(run_id,))]
        files={r['name']:storage.read_bytes(r['path'],r['sha256']) for r in records}
        if json.loads(files['manifest.json'])!=_manifest(run,records):raise ValueError('适配清单不一致。')
        if files['design.json']!=encoded or files['candidate.owl']!=raw:
            raise ValueError('应用运行与当前工件不一致。')
    return raw, encoded, doc


def save(storage, conn, version, doc):
    doc = deepcopy(doc)
    raw = storage.read_bytes(version['path'], version['payload_sha256'])
    with _RDF_LOCK:
        graph = Graph().parse(data=raw, format='xml')
        doc.update(current=_current(graph), payload_base64=base64.b64encode(raw).decode(),
            payload_sha256=version['payload_sha256'], output_asset_ref={k:version[k] for k in ('ontology_id','version_id')},
            parent_version_id=version['parent_version_id'])
        doc['current_sha256'] = json_digest(doc['current'])
        encoded = json_text(doc).encode()
        if restore_bundle(encoded) != raw: raise ValueError('应用保存校验失败。')
    path = 'applications/' + version['version_id'] + '/design.json'
    storage.write_bytes(path, encoded)
    conn.execute('INSERT INTO robotics_application_documents VALUES (?,?,?)', (version['version_id'],path,digest(encoded)))
    return encoded


def verify_sources(storage, conn, version):
    """Original XML is audit lineage; non-XML evidence attachments remain mandatory."""
    for sid in version['source_asset_ids']:
        row = _required(conn, 'SELECT * FROM sources WHERE source_asset_id=?', (sid,))
        if row['role'] not in ('behavior_tree','node_models'):
            storage.read_bytes(row['path'], row['sha256'])


class ApplicationAssets:
    def __init__(self, root, *, read_only=True):
        self.assets = AssetStore(root, read_only=read_only)
        self.storage = self.assets.storage

    def _load(self, oid, vid):
        with _RDF_LOCK, self.storage.transaction() as conn:
            version = _version(_required(conn,'SELECT * FROM versions WHERE ontology_id=? AND version_id=?',(oid,vid)))
            return load(self.storage,conn,version)

    def payload(self, ontology_id, version_id): return self._load(ontology_id,version_id)[0]
    def export_json(self, ontology_id, version_id): return self._load(ontology_id,version_id)[1]
    def detail(self, ontology_id, version_id): return self._load(ontology_id,version_id)[2]
