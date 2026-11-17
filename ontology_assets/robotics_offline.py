"""Read-only, self-contained export of an existing offline robotics version."""
import base64
import json

from rdflib import Graph
from rdflib.compare import to_canonical_graph

from domain_runs import RunStore
from domains.robotics._application_bundle import _current
from ontology_assets.governance import GovernanceService
from ontology_assets.payloads import json_text
from ontology_assets.store import AssetStore
from srv_context import _RDF_LOCK


class OfflineAssets:
    def __init__(self, root):
        self.assets = AssetStore(root, read_only=True)
        self.root = root

    def _file(self, record, raw):
        if len(raw) != record['byte_size']:
            raise ValueError('登记文件长度不一致。')
        public = {key: record[key] for key in (
            'artifact_id', 'name', 'kind', 'source_asset_id', 'role', 'media_type', 'sha256', 'byte_size'
        ) if key in record}
        return {**public, 'content_base64': base64.b64encode(raw).decode('ascii')}

    def _load(self, ontology_id, version_id):
        assets = self.assets
        version = assets.get_version(ontology_id, version_id)
        asset = assets.get_asset(ontology_id)
        metadata = version['metadata']
        if (asset['domain'] != 'robotics' or version['payload_kind'] != 'robotics_rdf'
                or 'design_contract' in metadata or 'application_contract' in metadata):
            raise ValueError('指定版本不是离线机器人本体。')
        raw = assets.read_payload(ontology_id, version_id)
        if len(raw) != version['byte_size'] or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
            raise ValueError('本体载荷无效。')
        with _RDF_LOCK:
            current = _current(to_canonical_graph(Graph().parse(data=raw, format='xml')))
        sources = [self._file(assets.get_source(sid), assets.read_source(sid))
                   for sid in version['source_asset_ids']]
        build = None
        run_id = metadata.get('build_run_id') or metadata.get('origin_build_run_id')
        if run_id:
            runs = RunStore(self.root, read_only=True)
            run = runs.get_run(run_id)
            baseline = metadata.get('source_baseline') or {}
            baseline_ref = {key: baseline.get(key) for key in ('ontology_id', 'version_id')}
            if (run['operation'] != 'build_ontology' or run['status'] != 'completed'
                    or not run['output_asset_ref'] or (run['output_asset_ref']['ontology_id'] != ontology_id
                    and run['output_asset_ref'] != baseline_ref)):
                raise ValueError('离线构建来源与资产不一致。')
            # An edited version may retain its original build. Keep that report explicitly
            # bound to its original output, never present it as a check of the edited payload.
            build = {'run_id': run_id, 'output_asset_ref': run['output_asset_ref'],
                     'result': run['result'], 'manifest': runs.manifest(run_id),
                     'artifacts': [self._file(item, runs.read_artifact(run_id, item['artifact_id']))
                                   for item in runs.artifacts(run_id)]}
            reports = [json.loads(base64.b64decode(item['content_base64']))
                       for item in build['artifacts'] if item['kind'] == 'validation']
            if reports != [run['result']]:
                raise ValueError('离线构建报告与运行记录不一致。')
        governance = GovernanceService(self.root, read_only=True)
        artifacts = [self._file(item, governance.read_artifact(ontology_id, version_id, item['artifact_id']))
                     for item in governance.artifacts(ontology_id, version_id)]
        document = {
            'contract': {'id': 'robotics-offline-bundle', 'version': 1},
            'output_asset_ref': {'ontology_id': ontology_id, 'version_id': version_id},
            'parent_version_id': version['parent_version_id'],
            'version_number': assets.version_number(ontology_id, version_id),
            'name': asset['name'], 'method': 'legacy_xml',
            'metadata': {key: metadata[key] for key in (
                'build_recipe', 'profile_id', 'build_run_id', 'origin_build_run_id',
                'source_baseline', 'merge', 'evidence_policy', 'mode')
                         if key in metadata},
            'payload_sha256': version['payload_sha256'],
            'payload_base64': base64.b64encode(raw).decode('ascii'),
            'current': current, 'sources': sources, 'build': build,
            'validation': version['validation'], 'artifacts': artifacts,
            'coverage': {'status': 'not_recorded'}, 'execution_readiness': 'not_verified',
        }
        return raw, document

    def export_json(self, ontology_id, version_id):
        return json_text(self._load(ontology_id, version_id)[1]).encode('utf-8')

    def payload(self, ontology_id, version_id):
        # Match the design/application readers: both download formats validate the
        # registered package, including its reports and source snapshots.
        return self._load(ontology_id, version_id)[0]
