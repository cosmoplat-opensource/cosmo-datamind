"""Durable application checks and transactional repair dispatch — DR-071."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import sqlite3
import uuid

from ontology_assets.storage import Storage
from srv_context import _RDF_LOCK
from . import evaluate, make_basis, compare_freshness, require_ready
from .models import ContractError, require, identifier, revision, digest


def validate(entrypoint, asset_refs, context, request_id=None):
    require(identifier(entrypoint))
    require(isinstance(asset_refs, list) and bool(asset_refs))
    seen = set()
    for ref in asset_refs:
        require(isinstance(ref, dict) and set(ref) == {'ontology_id', 'version_id'})
        require(all(identifier(v) for v in ref.values()) and ref['version_id'] != 'latest')
        key = (ref['ontology_id'], ref['version_id']); require(key not in seen); seen.add(key)
    require(isinstance(context, dict) and set(context) <= {'mode', 'task_ref'} and context.get('mode') in ('real', 'demo'))
    if context.get('task_ref') is not None:
        ref = context['task_ref']
        require(isinstance(ref, dict) and set(ref) == {'session_id'} and identifier(ref['session_id']))
    if request_id is not None: require(identifier(request_id))


class CheckService:
    def __init__(self, root, *, registry, resolver, read_only=False):
        from pathlib import Path
        self.root, self.registry, self.resolver, self.read_only = Path(root), registry, resolver, read_only
        if not read_only:
            self._storage()

    def _storage(self):
        try: return Storage(self.root, read_only=self.read_only)
        except (ValueError, OSError, sqlite3.Error):
            raise ContractError('storage_unavailable', '检查存储暂不可用。') from None

    def _report(self, conn, check_id):
        row = conn.execute('SELECT report FROM application_checks WHERE check_id=?', (check_id,)).fetchone()
        if row is None: raise ContractError('not_found', '检查报告不存在。')
        return json.loads(row[0])

    def _freshness(self, report):
        try:
            data = self.resolver.peek(deepcopy(report))
            if data is None: return 'unknown'
            try: spec = self.registry.resolve(report['entrypoint'])
            except ContractError: return 'unknown'
            basis = make_basis(application_id=spec['application_id'], contract_version=spec['contract_version'], **data)
            return compare_freshness(report, basis)
        except Exception: return 'unknown'

    def read(self, check_id):
        if not (self.root / 'domain.sqlite3').exists():
            raise ContractError('not_found', '检查报告不存在。')
        with self._storage().transaction() as conn: report = self._report(conn, check_id)
        return {'report': report, 'freshness': self._freshness(report)}

    def evaluate(self, *, entrypoint, asset_refs, context, request_id):
        require(not self.read_only and identifier(request_id)); validate(entrypoint, asset_refs, context, request_id)
        spec = self.registry.resolve(entrypoint)
        if spec['availability'] != 'available': raise ContractError('application_unavailable', '应用暂不可用。')
        fingerprint = digest({'entrypoint': entrypoint, 'asset_refs': asset_refs, 'context': context})
        try:
            with _RDF_LOCK, self._storage().transaction(write=True) as conn:
                old = conn.execute('SELECT fingerprint,check_id FROM application_check_requests WHERE request_id=?', (request_id,)).fetchone()
                if old:
                    require(old[0] == fingerprint, 'request_conflict')
                    report = self._report(conn, old[1]); replayed = True
                else:
                    data = self.resolver.resolve(entrypoint=entrypoint, asset_refs=deepcopy(asset_refs), context=deepcopy(context))
                    report = evaluate(registry=self.registry, entrypoint=entrypoint, **data)
                    report.update(schema_version=1, check_id='check_' + uuid.uuid4().hex,
                        checked_at=datetime.now(timezone.utc).isoformat())
                    require(self._freshness(report) == 'current', 'stale_result')
                    conn.execute('INSERT INTO application_checks VALUES (?,?)', (report['check_id'], json.dumps(report)))
                    conn.execute('INSERT INTO application_check_requests VALUES (?,?,?)', (request_id, fingerprint, report['check_id']))
                    replayed = False
            return {'report': report, 'freshness': self._freshness(report), 'replayed': replayed}
        except (OSError, sqlite3.Error):
            raise ContractError('storage_unavailable', '检查结果保存失败。') from None

    def repair(self, *, check_id, issue_id, action_id, input, expected_revision, request_id):
        require(not self.read_only and revision(expected_revision) and identifier(request_id))
        fingerprint = digest({'check_id': check_id, 'issue_id': issue_id, 'action_id': action_id,
                              'input': input, 'expected_revision': expected_revision})
        try:
            with _RDF_LOCK, self._storage().transaction(write=True) as conn:
                old = conn.execute('SELECT fingerprint,response FROM application_repair_requests WHERE request_id=?', (request_id,)).fetchone()
                if old:
                    require(old[0] == fingerprint, 'request_conflict')
                    return {**json.loads(old[1]), 'replayed': True}
                report = self._report(conn, check_id)
                action = next((a for i in report['issues'] if i['issue_id'] == issue_id
                               for a in i['repair_actions'] if a['action_id'] == action_id), None)
                require(action is not None, 'unknown_action')
                spec = self.registry.resolve(report['entrypoint'])
                handler = spec['repair_handlers'].get(action_id)
                require(action['availability'] == 'available' and handler is not None, 'repair_unavailable')
                require(self._freshness(report) == 'current', 'stale_result')
                target = action['target']
                require(expected_revision == target['expected_revision'] == self.resolver.resource_revision(target), 'stale_result')
                command = {'check_id': check_id, 'issue_id': issue_id, 'action_id': action_id, 'input': deepcopy(input),
                    'request_id': request_id, 'target': deepcopy(target),
                    'return_context': {'asset_refs': report['asset_refs'], 'task_ref': report['task_ref']}}
                response = {**handler(command, conn), 'replayed': False}
                conn.execute('INSERT INTO application_repair_requests VALUES (?,?,?)', (request_id, fingerprint, json.dumps(response)))
                return response
        except (OSError, sqlite3.Error):
            raise ContractError('storage_unavailable', '修复受理保存失败。') from None

    def require_ready(self, *, entrypoint, asset_refs, context, previous_report=None):
        validate(entrypoint, asset_refs, context)
        return require_ready(registry=self.registry, entrypoint=entrypoint,
            resolve_context=lambda: self.resolver.resolve(entrypoint=entrypoint, asset_refs=asset_refs, context=context),
            previous_report=previous_report)

    def summary(self, ontology_id, version_id):
        if not (self.root / 'domain.sqlite3').exists(): return {'status': 'not_assessed'}
        with self._storage().transaction() as conn:
            rows = conn.execute("SELECT report FROM application_checks WHERE json_extract(report,'$.application_id')=? "
                "AND json_array_length(report,'$.asset_refs')=1 AND json_extract(report,'$.asset_refs[0].ontology_id')=? "
                "AND json_extract(report,'$.asset_refs[0].version_id')=? ORDER BY rowid DESC LIMIT 1",
                ('robotics.pick_place', ontology_id, version_id)).fetchall()
        for row in rows:
            report = json.loads(row[0])
            if report['application_id'] == 'robotics.pick_place' and report['asset_refs'] == [{'ontology_id': ontology_id, 'version_id': version_id}]:
                return {'status': 'supported' if report['preparation_status'] == 'available' else 'unsupported',
                    'check_id': report['check_id'], 'freshness': self._freshness(report),
                    'reasons': [i['message'] for i in report['issues']]}
        return {'status': 'not_assessed'}
