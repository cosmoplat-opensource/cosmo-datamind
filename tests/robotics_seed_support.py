"""Process-local immutable test preconditions; consumer operations remain uncached."""
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
import sqlite3
import tempfile
from threading import Lock

from rdflib import Graph

from tests.robotics_governance_support import actual_build, NOW, evidence_spec
from tests.robotics_support import SOURCE, MODELS, S


class SeedCache:
    """Build once, then give each caller independent SQLite and regular-file bytes."""
    def __init__(self):
        self._entries = {}
        self._lock = Lock()

    def _entry(self, key, build):
        with self._lock:
            if key not in self._entries:
                with tempfile.TemporaryDirectory(prefix='robotics-test-seed-') as raw:
                    root = Path(raw) / 'seed'
                    result = build(root)
                    files = {}
                    for path in sorted(root.rglob('*')):
                        if path.is_symlink():
                            raise ValueError('Test seeds cannot contain symlinks')
                        if not path.is_file() or path.name in ('domain.sqlite3', 'domain.sqlite3-wal',
                                                               'domain.sqlite3-shm', 'domain.sqlite3-journal'):
                            continue
                        files[str(path.relative_to(root))] = path.read_bytes()
                    database = root / 'domain.sqlite3'
                    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as source:
                        with sqlite3.connect(Path(raw) / 'backup.sqlite3') as backup:
                            source.backup(backup)
                    files['domain.sqlite3'] = (Path(raw) / 'backup.sqlite3').read_bytes()
                    # Store immutable file bytes and a detached result only after successful construction.
                    self._entries[key] = (files, deepcopy(result))
            return self._entries[key]

    def restore(self, key, destination, build):
        destination = Path(destination)
        if destination.exists():
            raise FileExistsError('Test seed destination already exists')
        files, result = self._entry(key, build)
        destination.mkdir(parents=True, exist_ok=False)
        for relative, content in files.items():
            path = destination / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        return deepcopy(result)


_BUILD_SEEDS = SeedCache()


def built_seed(root):
    root = Path(root)
    # Existing assets, request identities and failure-injection setup must be preserved.
    if root.exists():
        return actual_build(root)
    source, models = SOURCE.read_bytes(), MODELS.read_bytes()
    key = (source, models, actual_build)
    files, (_version, seed_run) = _BUILD_SEEDS._entry(key, actual_build)
    # Reuse only immutable computation output. Admission/save assign new identities,
    # bind the actual source bytes and perform the full semantic validation again.
    from domain_runs import RunStore
    from domain_runs.build_service import BuildService
    from ontology_assets import AssetStore
    run = BuildService(root).submit(name='new source', recipe='bt_xml', profile_id='ur_pick_place',
        request_id='new source', sources={
            'behavior_tree': {'filename': 'tree.xml', 'content': source},
            'node_models': {'filename': 'models.xml', 'content': models}})
    runs = RunStore(root)
    runs.start(run['run_id'])
    saved = runs.finish_build(run['run_id'], result=deepcopy(seed_run['result']),
                              ontology=files[_version['path']])
    return AssetStore(root).get_version(**saved['output_asset_ref']), saved


@lru_cache(maxsize=1)
def ready_payload():
    """Real normalization/evidence of a fixed synthetic graph, shared as immutable bytes."""
    from domains.robotics import evidence
    from domains.robotics.publisher import publish_ontology
    from tests.unit.test_robotics_task_completion import ready_graph
    # Constants stay with the task fixture; importing here avoids a module cycle.
    from tests.robotics_task_support import BASIS, EXPIRY
    with tempfile.TemporaryDirectory(prefix='robotics-ready-seed-') as raw:
        target = Path(raw) / 'ready.owl'
        publish_ontology(ready_graph(), target, mode='real')
        graph = Graph().parse(data=target.read_bytes(), format='xml')
    for index, kind, implementation, slot in [
        (0, 'grasp_compatibility', S.grasp_Subtree_Impl, None),
        (1, 'placement_compatibility', S.Place_Subtree_Impl, str(S.Slot_B_01)),
    ]:
        subject = S[f'TaskCompletionEvidence{index}']
        graph.remove((subject, None, None))
        spec = evidence_spec(iri=str(subject), kind=kind, implementation=str(implementation),
                             slot=slot, reference=BASIS, valid_until=EXPIRY)
        graph = evidence.register_evidence(graph, spec, mode='real', now=NOW)
        graph = evidence.review_evidence(graph, str(subject), decision='accepted',
            actor='synthetic fixture', reason=BASIS, now=NOW)
    return graph.serialize(format='xml', encoding='utf-8')
