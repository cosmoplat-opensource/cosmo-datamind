"""Content-bound application decisions and read-only freshness comparison — DR-071 §5."""
from copy import deepcopy
import hashlib

from .models import ContractError, canonical, digest, identifier, require, revision


def make_basis(*, application_id, contract_version, snapshots, task, context, environment):
    """Inputs come from trusted server resolvers, never HTTP readiness claims."""
    require(identifier(application_id) and identifier(contract_version))
    require(isinstance(snapshots, list) and bool(snapshots))
    assets = []
    seen = set()
    for snapshot in snapshots:
        require(isinstance(snapshot, dict))
        oid, vid = snapshot.get('ontology_id'), snapshot.get('version_id')
        require(identifier(oid) and identifier(vid) and vid != 'latest')
        require((oid, vid) not in seen)
        seen.add((oid, vid))
        require(identifier(snapshot.get('domain')) and identifier(snapshot.get('payload_kind')))
        require(isinstance(snapshot.get('payload'), bytes) and bool(snapshot['payload']))
        require(revision(snapshot.get('lifecycle_revision')))
        assets.append({'ontology_id': oid, 'version_id': vid, 'domain': snapshot['domain'],
                       'payload_kind': snapshot['payload_kind'],
                       'payload_sha256': hashlib.sha256(snapshot['payload']).hexdigest(),
                       'lifecycle_revision': snapshot['lifecycle_revision']})
    require(isinstance(context, dict))
    # Keep the pre-existing independent dimensions; do not downgrade strict to offline.
    require(context.get('mode') in ('real', 'demo'))
    require(context.get('generation_policy') in ('offline', 'strict'))
    canonical(context)
    require(isinstance(environment, dict) and bool(environment))
    canonical(environment)
    task_ref, task_hash = None, None
    if task is not None:
        require(isinstance(task, dict) and identifier(task.get('session_id')))
        require(revision(task.get('revision')) and isinstance(task.get('content'), dict) and bool(task['content']))
        task_ref = {'session_id': task['session_id'], 'revision': task['revision']}
        task_hash = digest(task['content'])
    return {'schema_version': '1', 'application_id': application_id, 'contract_version': contract_version,
            'assets': assets, 'task_ref': task_ref, 'task_hash': task_hash,
            'context': deepcopy(context), 'environment_revision': deepcopy(environment)}


def compare_freshness(report, current_basis):
    """No IO, evaluation or report mutation. Unknown information cannot mean current."""
    if (not isinstance(report, dict) or not isinstance(report.get('basis'), dict)
            or not isinstance(current_basis, dict)):
        return 'unknown'
    required = {'schema_version', 'application_id', 'contract_version', 'assets', 'task_ref',
                'task_hash', 'context', 'environment_revision'}
    if not required <= current_basis.keys() or not required <= report['basis'].keys():
        return 'unknown'
    if not current_basis['environment_revision'] or not current_basis['assets']:
        return 'unknown'
    try:
        return 'current' if canonical(report['basis']) == canonical(current_basis) else 'stale'
    except ContractError:
        return 'unknown'
