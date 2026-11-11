"""Read fixed design candidates without UR rendering or model side effects — DR-070."""
import json

from rdflib import Graph, Literal, OWL, RDF, RDFS, URIRef

from domain_runs import RunStore
from domain_runs import robotics_design_contracts as contracts
from domains.robotics.consumer import load_ontology_bytes
from srv_context import _RDF_LOCK
from .store import AssetStore


class DesignAssets:
    def __init__(self, root, *, read_only=True):
        self.assets = AssetStore(root, read_only=read_only)
        self.runs = RunStore(root, read_only=read_only)

    def _load(self, ontology_id, version_id):
        version = self.assets.get_version(ontology_id, version_id)
        metadata = version['metadata']
        if metadata.get('design_contract') != contracts.DESIGN_CONTRACT or version['payload_kind'] != 'robotics_rdf':
            raise ValueError('指定版本不是设计候选。')
        run = self.runs.get_run(metadata['build_run_id'])
        ref = {'ontology_id': ontology_id, 'version_id': version_id}
        if (run['operation'] != 'extract_abox' or run['status'] != 'completed' or run['output_asset_ref'] != ref
                or metadata['session_ref'] != run['session_ref']
                or metadata['design_revision'] != run['design_input']['revision']
                or version['source_asset_ids'] != [s['source_asset_id'] for s in run['design_input']['sources']]):
            raise ValueError('设计版本与运行／修订不一致。')
        manifest = self.runs.manifest(run['run_id'])
        if {a['name'] for a in manifest['artifacts']} != {'candidate.owl', 'design.json'}:
            raise ValueError('设计运行缺少完整 JSON 或 OWL。')
        files = {row['name']: self.runs.read_artifact(run['run_id'], row['artifact_id'])
                 for row in self.runs.artifacts(run['run_id']) if row['name'] != 'manifest.json'}
        ontology = self.assets.read_payload(ontology_id, version_id)
        if files['candidate.owl'] != ontology:
            raise ValueError('运行与版本的本体字节不一致。')
        document = json.loads(files['design.json'])
        if (document['output_asset_ref'] != ref or document['validation'] != run['result']
                or document['payload_sha256'] != version['payload_sha256']):
            raise ValueError('设计 JSON 与版本不一致。')
        with self.assets.storage.transaction() as conn:
            contracts.verify_input(self.assets.storage, conn, run)
        contracts.validate_candidate(document, ontology, run)
        return version, document, files

    def detail(self, ontology_id, version_id):
        version, document, _ = self._load(ontology_id, version_id)
        return {'version': version, 'tbox': document['state']['schema_confirmation']['tbox'],
                'abox': document['abox'], 'report': document['coverage'],
                'session_ref': document['session_ref'], 'revision': document['revision'],
                'validation': document['validation']}

    def export_json(self, ontology_id, version_id):
        return self._load(ontology_id, version_id)[2]['design.json']

    def payload(self, ontology_id, version_id):
        return self._load(ontology_id, version_id)[2]['candidate.owl']

    def compatibility(self, ontology_id, version_id):
        sha = None
        try:
            version = self.assets.get_version(ontology_id, version_id)
            sha = version['payload_sha256']
            if version['metadata'].get('design_contract') is not None:
                raw = self.payload(ontology_id, version_id)
                try:
                    with _RDF_LOCK:
                        load_ontology_bytes(raw, mode='real')
                except ValueError:
                    return {'status': 'unsupported', 'reasons': ['本体设计有效，但未满足现有 UR 行为树应用契约。'],
                            'payload_sha256': sha}
            else:
                self.assets.load_robotics_application(ontology_id, version_id, mode='real')
            return {'status': 'supported', 'reasons': [], 'payload_sha256': sha}
        except (ValueError, KeyError, OSError, TypeError):
            return {'status': 'invalid', 'reasons': ['固定版本或来源／设计工件缺失、损坏或契约无效。'], 'payload_sha256': sha}

    def graph(self, ontology_id, version_id):
        raw = self.payload(ontology_id, version_id)
        with _RDF_LOCK:
            graph = Graph().parse(data=raw, format='xml')
        from domains.robotics.views import BUSINESS, SKILLS, local
        from domains.robotics.graph import CLASSES
        nodes = []
        kinds = ((OWL.Class, 'class'), (OWL.ObjectProperty, 'object_property'),
                 (OWL.DatatypeProperty, 'data_property'), (OWL.NamedIndividual, 'individual'))
        seen = set()
        for term, kind in kinds:
            for subject in sorted(set(graph.subjects(RDF.type, term)), key=str):
                if not isinstance(subject, URIRef) or subject in seen:
                    continue
                seen.add(subject)
                properties = [{'predicate': str(p), 'value': str(o)} for p, o in sorted(graph.predicate_objects(subject),
                    key=lambda item: tuple(map(str, item))) if isinstance(o, Literal)]
                types = sorted({local(t) for t in graph.objects(subject, RDF.type) if t != OWL.NamedIndividual}) if kind == 'individual' else []
                labels = {local(t): str(graph.value(t, RDFS.label) or local(t)) for t in graph.objects(subject, RDF.type) if t != OWL.NamedIndividual}
                type_labels = [CLASSES.get(t, labels[t]) for t in types]
                group = 'business' if BUSINESS.intersection(types) else 'skills' if SKILLS.intersection(types) else 'other'
                nodes.append({'id': str(subject), 'kind': kind, 'label': str(graph.value(subject, RDFS.label) or subject),
                              'types': types, 'type_labels': type_labels, 'group': group, 'properties': properties})
        edges = [{'source': str(s), 'target': str(o), 'predicate': str(p)} for s, p, o in sorted(graph, key=lambda t: tuple(map(str, t)))
                 if s in seen and o in seen]
        return {'nodes': nodes, 'edges': edges}
