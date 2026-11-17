"""Read-only effective-IR adapter for the existing QA consumer — DR-074 / IR-028 T03."""
from copy import deepcopy
import json

from ontology_assets.payloads import data_ir
from .models import ContractError, canonical, digest, identifier, require


def _row(code, status='passed', issues=None, reason=None):
    return [{'check_code': code, 'status': status, 'applicable': True,
             'issues': issues or [], **({'reason': reason} if reason else {})}]


def _problem(data, code, layer, message, *, entities, severity='blocking'):
    return {'code': code, 'layer': layer, 'severity': severity, 'message': message,
            'expected': '使用当前本体可查询子集及既有 SQL／指标口径', 'observed': entities,
            'entity_refs': entities,
            'evidence_refs': [digest(json.loads(s['payload'])) for s in data['snapshots']],
            'repair_actions': []}


class DataQAAdapter:
    """Host callbacks are trusted server functions; nothing here runs a business query."""
    def __init__(self, host, *, assets=None, allow_deleted=False):
        self.host, self.assets, self.allow_deleted = host, assets, allow_deleted

    def descriptor(self):
        return {'application_id': 'data.deep_qa', 'contract_version': '1', 'entrypoints': ['data.ask'],
                'accepted_payload_kinds': ['datamind_ir'], 'asset_cardinality': {'min': 1, 'max': 64},
                'availability': 'available', 'executor_id': 'data.ask', 'repair_handlers': {},
                'checkers': [{'layer': layer, 'required_codes': [code], 'required': True,
                              'depends_on': deps, 'check': check}
                             for layer, code, deps, check in [
                                 ('schema', 'data.bindings', [], self._schema),
                                 ('execution', 'data.queryable', ['data.bindings'], self._execution),
                                 ('task', 'data.sql_plan', ['data.bindings', 'data.queryable'], self._task),
                                 ('environment', 'data.connections', [], self._environment)]]}

    def _snapshot(self, key, ir):
        raw = canonical(ir).encode()
        data_ir(raw)
        return {'ontology_id': key, 'version_id': 'effective_' + digest(ir),
                'domain': 'data', 'payload_kind': 'datamind_ir', 'payload': raw, 'lifecycle_revision': 0}

    def _versions(self, refs):
        require(self.assets is not None and isinstance(refs, list) and 1 <= len(refs) <= 64)
        snapshots, documents, seen = [], [], set()
        for ref in refs:
            require(isinstance(ref, dict) and set(ref) == {'ontology_id', 'version_id'})
            require(all(identifier(v) for v in ref.values()) and ref['version_id'] != 'latest')
            pair = (ref['ontology_id'], ref['version_id'])
            require(pair not in seen); seen.add(pair)
            asset = self.assets.get_asset(ref['ontology_id'])
            from ontology_assets.lifecycle import state_in, require_active
            with self.assets.storage.transaction() as conn:
                life=state_in(conn,ref['ontology_id'])
                if not self.allow_deleted:require_active(conn,ref['ontology_id'])
            version = self.assets.get_version(**ref)
            require(asset['domain'] == 'data' and version['payload_kind'] == 'datamind_ir', 'unsupported_payload')
            raw = self.assets.read_payload(**ref)
            documents.append(data_ir(raw))
            snapshots.append({**ref, 'domain': 'data', 'payload_kind': 'datamind_ir',
                              'payload': raw, 'lifecycle_revision': life['revision']})
        if len(documents) == 1:
            return snapshots, documents[0]
        # Match the legacy multi-graph consumer: first object id/edge endpoint pair wins.
        objects, links, seen_objects, seen_links = [], [], set(), set()
        for doc in documents:
            rels, sk, tk = self.host._rels(doc)
            for index, obj in enumerate(doc['objects']):
                key = self.host._obj_key(obj, index)
                if key not in seen_objects:
                    seen_objects.add(key); objects.append({**obj, 'id': key})
            for rel in rels:
                pair = (rel.get(sk), rel.get(tk))
                if all(pair) and pair not in seen_links:
                    seen_links.add(pair); links.append({**rel, 'source': pair[0], 'target': pair[1]})
        return snapshots, {'objects': objects, 'links': links}

    def resolve(self, *, graph_keys=None, asset_refs=None, question=None, plan=None, focus_tables=None):
        """Freeze actual edited content, ordered input vector and current local query catalog."""
        require(asset_refs is None or graph_keys is None)
        require(graph_keys is None or isinstance(graph_keys, list))
        require(question is None or isinstance(question, str) and len(question) <= 20000)
        require(focus_tables is None or isinstance(focus_tables, list) and
                all(isinstance(t, str) and bool(t.strip()) for t in focus_tables))
        if plan is not None:
            require(isinstance(plan, dict) and isinstance(plan.get('analyses'), list))
            require(len(plan['analyses']) <= 4)
            require(all(isinstance(a, dict) and isinstance(a.get('sql'), str) and bool(a['sql'].strip())
                        for a in plan['analyses']))
            canonical(plan)
        try:
            requested = list(dict.fromkeys(graph_keys or []))
            require(all(identifier(k) and not self.host._bad_gkey(k) for k in requested))
            require(len(requested) <= 64)
            if asset_refs is not None:
                snapshots, ir = self._versions(asset_refs)
                actual, fallback = [r['ontology_id'] for r in asset_refs], ''
            else:
                ir, actual, fallback = self.host._anchor_ir(requested)
                ir = deepcopy(ir)
                snapshots = []
                for key in actual:
                    effective = self.host.load_ir_edited(key) or self.host.load_ir(key)
                    # Legacy multi-graph anchor retains absent keys; preserve an empty scope entry.
                    snapshots.append(self._snapshot(key, effective or {'objects': [], 'links': []}))
            data_ir(canonical(ir).encode())
            inventory = self.host._qa_table_inventory()
            profile = self.host._qa_graph_profile(ir, inventory)
            query_ir = deepcopy(self.host._normalize_tables(ir))
            main = {t.lower() for t in profile['available_tables'] if not t.lower().startswith('up.')}
            uploads = {t[3:].lower() for t in profile['available_tables'] if t.lower().startswith('up.')}
            for obj in query_ir['objects']:
                table = str(self.host._obj_table(obj) or '').strip()
                if not table:
                    continue
                if table.lower() in main:
                    obj['table'] = table
                elif table.lower() in uploads:
                    obj['table'] = 'up.' + table
                else:
                    obj['qa_unavailable_table'] = table
                    obj.pop('table', None); obj['tables'] = []
            focus = deepcopy(focus_tables or [])
            info = {'requested_keys': requested, 'actual_keys': actual, 'fallback': fallback,
                    'ir': query_ir, 'profile': profile, 'focus_tables': focus,
                    'strict': asset_refs is not None or bool(requested and actual != ['demo'] and not fallback),
                    'metric_cards': self.host._metric_cards(question or '', query_ir)}
            environment = {'adapter_revision': 'data-deep-qa-1', 'deep_qa': info,
                           'catalog': [sorted(inventory[0]), sorted(inventory[1])],
                           'connection_revision': digest([list(item) for item in self.host._stat_sig(self.host.DB, self.host.UPLOAD_DB)])}
            task = None if not question or not question.strip() else {
                'session_id': 'data-question', 'revision': 0,
                'content': {'question': question, 'plan': deepcopy(plan)}}
            return {'snapshots': snapshots, 'task': task,
                    'context': {'mode': 'real', 'generation_policy': 'offline'}, 'environment': environment}
        except ContractError:
            raise
        except KeyError:
            raise ContractError('not_found', '数据本体固定版本不存在。') from None
        except Exception:
            raise ContractError('resolution_failed', '无法取得当前数据本体或连接目录，请检查数据源。') from None

    def evaluate(self, **kwargs):
        from . import evaluate
        from .adapters import registry
        return evaluate(registry=registry(data_qa=self), entrypoint='data.ask', **self.resolve(**kwargs))

    def offline_plan(self, data):
        info = data['environment']['deep_qa']
        return self.host.fallback_plan(data['task']['content']['question'], ir=deepcopy(info['ir']),
                                       focus_tables=deepcopy(info['focus_tables']))

    def _schema(self, data):
        info = data['environment']['deep_qa']
        foreign = any(s['domain'] != 'data' or s['payload_kind'] != 'datamind_ir' for s in data['snapshots'])
        if foreign:
            return _row('data.bindings', 'unsupported', [_problem(data, 'schema.unsupported_payload', 'schema',
                        '仅数据 JSON IR 支持问数。', entities=info['actual_keys'])])
        if info['profile']['bound_tables']:
            return _row('data.bindings')
        entities = [str(o.get('id') or o.get('name') or i) for i, o in enumerate(info['ir']['objects'])]
        return _row('data.bindings', 'missing', [_problem(data, 'data.table_unbound', 'schema',
                    '请在本体构建或评审中完成对象与数据表绑定。', entities=entities or info['actual_keys'])])

    def _execution(self, data):
        info = data['environment']['deep_qa']
        if info['profile']['queryable']:
            return _row('data.queryable')
        return _row('data.queryable', 'missing', [_problem(data, 'data.table_unavailable', 'execution',
                    '绑定表未接入当前问数连接，请在数据连接中接入并物化这些表。',
                    entities=info['profile']['unavailable_tables'])])

    def _task(self, data):
        info = data['environment']['deep_qa']
        plan = data['task']['content']['plan']
        if not plan or not plan['analyses']:
            return _row('data.sql_plan', 'not_evaluated', reason='尚未提供可检查的 SQL 计划。')
        issues = []
        for index, analysis in enumerate(plan['analyses']):
            sql = analysis['sql']
            if not self.host.sql_is_readonly(sql) or not self.host._single_statement(sql):
                ok = False
            else:
                ok, _ = self.host._qa_validate_sql(sql, info['ir'], strict=info['strict'],
                                                  focus_tables=info['focus_tables'])
            if not ok:
                issues.append(_problem(data, 'data.sql_rejected', 'task',
                    '查询未通过只读、表列范围或既有连接口径校验，请调整查询计划或修订绑定。',
                    entities=['analysis-' + str(index + 1)]))
        return _row('data.sql_plan', 'invalid' if issues else 'passed', issues)

    def _environment(self, data):
        info = data['environment']['deep_qa']; issues = []
        if info['profile']['queryable'] and info['profile']['unavailable_tables']:
            issues.append(_problem(data, 'data.table_unavailable', 'environment',
                '部分绑定表未接入当前问数连接；可问可用子集，如需这些表请接入并物化。',
                entities=info['profile']['unavailable_tables'], severity='warning'))
        if info['fallback']:
            issues.append(_problem(data, 'data.legacy_fallback', 'environment', info['fallback'],
                                   entities=info['actual_keys'], severity='warning'))
        return _row('data.connections', issues=issues)
