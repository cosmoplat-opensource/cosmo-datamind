"""Deterministic layered evaluation and execution guard — DR-071 §3–6."""
from copy import deepcopy

from .freshness import make_basis
from .models import ContractError, LAYERS, STATUSES, canonical, identifier, require, revision


def _issue(code, layer, message):
    return {'code': code, 'layer': layer, 'severity': 'blocking', 'message': message,
            'expected': '满足当前应用契约', 'observed': None, 'entity_refs': [],
            'evidence_refs': [], 'repair_actions': []}


def _outcome(checker, code, status, *, reason=None, issue=None):
    return {'check_code': code, 'status': status, 'applicable': True, 'required': checker['required'],
            'reason': reason, 'issues': [] if issue is None else [issue]}


def _normalize_issue(raw, layer, handlers):
    require(isinstance(raw, dict))
    require(identifier(raw.get('code')) and raw.get('layer') == layer)
    require(raw.get('severity') in ('blocking', 'warning'))
    require(isinstance(raw.get('message'), str) and bool(raw['message'].strip()))
    require('expected' in raw and 'observed' in raw)
    for field in ('entity_refs', 'evidence_refs', 'repair_actions'):
        require(isinstance(raw.get(field), list))
    issue = {field: deepcopy(raw[field]) for field in
             ('code', 'layer', 'severity', 'message', 'expected', 'observed', 'entity_refs', 'evidence_refs')}
    actions = []
    for item in raw['repair_actions']:
        require(isinstance(item, dict) and identifier(item.get('action_id')))
        require(isinstance(item.get('label'), str) and bool(item['label'].strip()))
        require(item.get('kind') in ('navigate', 'submit'))
        target = item.get('target')
        require(isinstance(target, dict) and identifier(target.get('resource_kind')))
        require(identifier(target.get('resource_id')) and revision(target.get('expected_revision')))
        # Suggested actions cannot invent handlers or inject a navigable URL.
        available = item.get('availability') == 'available' and item['action_id'] in handlers
        actions.append({'action_id': item['action_id'], 'label': item['label'], 'kind': item['kind'],
                        'availability': 'available' if available else 'unavailable', 'target': deepcopy(target)})
    issue['repair_actions'] = actions
    canonical(issue)
    return issue


def _normalize(raw, checker, handlers):
    require(isinstance(raw, list))
    require(all(isinstance(row, dict) and identifier(row.get('check_code')) for row in raw))
    codes = [row['check_code'] for row in raw]
    require(len(codes) == len(set(codes)) and set(codes) == set(checker['required_codes']))
    rows = []
    for row in raw:
        require(row.get('status') in STATUSES and type(row.get('applicable')) is bool)
        reason = row.get('reason')
        if not row['applicable'] or row['status'] == 'not_evaluated':
            require(isinstance(reason, str) and bool(reason.strip()))
        require(isinstance(row.get('issues'), list))
        issues = [_normalize_issue(i, checker['layer'], handlers) for i in row['issues']]
        # A conflicting passed result or false applicability cannot hide blocking evidence.
        require(not ((row['status'] == 'passed' or not row['applicable'])
                     and any(i['severity'] == 'blocking' for i in issues)))
        normalized = _outcome(checker, row['check_code'], row['status'], reason=reason)
        normalized.update(applicable=row['applicable'], issues=issues)
        if not issues and row['applicable'] and row['status'] not in ('passed', 'not_evaluated'):
            issue = _issue(row['check_code'], checker['layer'], '该检查项未满足应用要求。')
            if not checker['required'] and row['status'] != 'error':
                issue['severity'] = 'warning'
            normalized['issues'] = [issue]
        rows.append(normalized)
    return rows


def _satisfied(row):
    # Checker failures always block; a warning cannot lower a required check's severity.
    if row['status'] == 'error' or any(i['severity'] == 'blocking' for i in row['issues']):
        return False
    if not row['applicable']:
        return True
    return not row['required'] or row['status'] == 'passed'


def _run(spec, data, unsupported):
    outcomes = {}
    for checker in spec['checkers']:
        reason = None
        if unsupported:
            reason = '该载荷类型不支持当前应用。'
        elif checker['layer'] == 'task' and data['task'] is None:
            reason = '尚未提供当前任务。'
        elif any(not (outcomes[dep]['status'] == 'passed' or not outcomes[dep]['applicable'])
                 or outcomes[dep]['status'] == 'error' for dep in checker['depends_on']):
            reason = '前置检查尚未通过。'
        if reason is not None:
            status = 'unsupported' if unsupported and checker['layer'] == 'schema' else 'not_evaluated'
            issue = _issue('schema.unsupported_payload', 'schema', reason) if status == 'unsupported' else None
            rows = [_outcome(checker, code, status, reason=reason, issue=deepcopy(issue))
                    for code in checker['required_codes']]
        else:
            try:
                rows = _normalize(checker['check'](deepcopy(data)), checker, spec['repair_handlers'])
            except Exception:
                # 依据 DR-071 第 6 条：异常不变成 missing，也不将原始异常暴露到报告。
                rows = [_outcome(checker, code, 'error', issue=_issue(
                    'system.checker_failed', checker['layer'], '检查器异常或返回结果不完整，请重试检查。'))
                        for code in checker['required_codes']]
        for row in rows:
            outcomes[row['check_code']] = row
    return outcomes


def evaluate(*, registry, entrypoint, snapshots, task, context, environment):
    """Evaluate a fixed server snapshot, without model calls, storage or execution."""
    spec = registry.resolve(entrypoint)
    if spec['availability'] != 'available':
        raise ContractError('application_unavailable', '该应用检查器尚未接通。')
    data = deepcopy({'snapshots': snapshots, 'task': task, 'context': context, 'environment': environment})
    basis = make_basis(application_id=spec['application_id'], contract_version=spec['contract_version'], **data)
    cardinality = spec['asset_cardinality']
    require(cardinality['min'] <= len(data['snapshots']) <= cardinality['max'])
    unsupported = any(s['payload_kind'] not in spec['accepted_payload_kinds'] for s in data['snapshots'])
    outcomes = _run(spec, data, unsupported)
    layers = {layer: {'checks': [], 'summary': 'passed'} for layer in LAYERS}
    issues, actions = [], []
    preparation = True
    complete = True
    for checker in spec['checkers']:
        for code in checker['required_codes']:
            row = outcomes[code]
            satisfied = _satisfied(row)
            complete = complete and satisfied
            if checker['layer'] in ('schema', 'execution'):
                preparation = preparation and satisfied
            public = {k: deepcopy(v) for k, v in row.items() if k != 'issues'}
            public['issue_ids'] = []
            for issue in row['issues']:
                issue = deepcopy(issue)
                issue['issue_id'] = f'issue-{len(issues) + 1}'
                issues.append(issue)
                public['issue_ids'].append(issue['issue_id'])
                for action in issue['repair_actions']:
                    if action['availability'] == 'available' and action['action_id'] not in actions:
                        actions.append(action['action_id'])
            layers[checker['layer']]['checks'].append(public)
    for value in layers.values():
        statuses = [row['status'] for row in value['checks']]
        value['summary'] = next((s for s in ('error', 'invalid', 'unsupported', 'missing', 'not_evaluated')
                                 if s in statuses), 'passed')
    return {'schema_version': '1', 'entrypoint': entrypoint, 'application_id': spec['application_id'],
            'contract_version': spec['contract_version'], 'basis': basis,
            'asset_refs': [{'ontology_id': a['ontology_id'], 'version_id': a['version_id']} for a in basis['assets']],
            'payload_hashes': [a['payload_sha256'] for a in basis['assets']],
            'task_ref': deepcopy(basis['task_ref']), 'task_hash': basis['task_hash'],
            'context': deepcopy(basis['context']), 'environment_revision': deepcopy(basis['environment_revision']),
            'preparation_status': 'available' if preparation else 'blocked',
            'execution_status': 'not_evaluated' if task is None else ('ready' if complete else 'blocked'),
            'execution_readiness': 'not_verified', 'layers': layers, 'issues': issues, 'allowed_actions': actions}


def require_ready(*, registry, entrypoint, resolve_context, previous_report=None):
    """A previous report is traceability only, never authorization for execution."""
    registry.resolve(entrypoint)
    try:
        data = resolve_context()
        require(isinstance(data, dict) and set(data) == {'snapshots', 'task', 'context', 'environment'})
        report = evaluate(registry=registry, entrypoint=entrypoint, **data)
    except ContractError:
        raise
    except Exception:
        raise ContractError('application_not_ready', '无法取得当前应用检查依据。') from None
    if report['execution_status'] != 'ready':
        raise ContractError('application_not_ready', '当前任务尚未满足应用契约，请检查并补全缺项。')
    return report
