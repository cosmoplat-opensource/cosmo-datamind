"""Transactional version governance, precise reviews and source merging — DR-059 / DR-060 / DR-061 / DR-064.

Actors are declarations, not authenticated identities. Only local registered
sources are dereferenced; references/URLs are never fetched by this service.
"""
from contextlib import contextmanager
from copy import deepcopy
import json
import uuid

from rdflib import Graph, Literal, RDF, XSD

from domains.robotics import authoring, consumer, evidence, version_diff, views
from domains.robotics.graph import S
from srv_context import _RDF_LOCK

from .payloads import json_text, validate_payload
from .storage import digest
from .store import AssetStore, _required, _version


class GovernanceError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _uid(prefix):
    return prefix + uuid.uuid4().hex


def _hash(value):
    return digest(json_text(value).encode())


class GovernanceService:
    def __init__(self, root, *, read_only=False):
        self.assets = AssetStore(root, read_only=read_only)
        self.storage = self.assets.storage

    @contextmanager
    def _atomic(self, *, write=False):
        # Consistent lock order, shared with existing RDF work. No model/network call.
        try:
            with _RDF_LOCK, self.storage.transaction(write=write) as conn:
                yield conn
        except GovernanceError:
            raise
        except KeyError as exc:
            raise GovernanceError('not_found', '资产、版本或记录不存在。') from exc
        except OSError as exc:
            raise GovernanceError('integrity_conflict', '登记文件不可读或无法完整写入。') from exc
        except ValueError as exc:
            raise GovernanceError('invalid_request', str(exc)) from exc

    def _asset(self, conn, oid):
        asset = _required(conn, 'SELECT * FROM assets WHERE ontology_id=?', (oid,))
        if asset['domain'] != 'robotics': raise GovernanceError('invalid_request', '治理服务仅接收机器人资产。')
        return asset

    def _version(self, conn, oid, vid):
        self._asset(conn, oid)
        return _version(_required(conn, 'SELECT * FROM versions WHERE ontology_id=? AND version_id=?', (oid,vid)))

    def _bytes(self, record):
        try:
            content = self.storage.read_bytes(record['path'], record['payload_sha256'])
            if len(content) != record['byte_size']: raise ValueError('size mismatch')
            return content
        except (ValueError,OSError) as exc:
            raise GovernanceError('integrity_conflict', '本体字节与固定版本不一致。') from exc

    def _sources(self, conn, ids):
        rows = []
        for sid in ids:
            row = _required(conn, 'SELECT * FROM sources WHERE source_asset_id=?', (sid,))
            try:
                content = self.storage.read_bytes(row['path'], row['sha256'])
                if len(content) != row['byte_size']: raise ValueError('size mismatch')
            except (ValueError, OSError) as exc:
                raise GovernanceError('integrity_conflict', '来源或证据附件已经损坏。') from exc
            rows.append(row)
        return rows

    def _graph(self, conn, record):
        content = self._bytes(record)
        self._sources(conn, record['source_asset_ids'])
        validate_payload('robotics', 'robotics_rdf', content)
        graph = Graph().parse(data=content, format='xml')
        if record['metadata'].get('evidence_policy') and evidence.policy(graph) != evidence.POLICY:
            raise GovernanceError('integrity_conflict', '治理版本不能移除受控证据策略。')
        consumer.check_mode(graph, self._mode(record, graph))
        return graph

    @staticmethod
    def _mode(record, graph):
        return record['publication_mode'] or record['metadata'].get('mode') or (
            'demo' if str(graph.value(evidence.ONTOLOGY, S.datasetKind)) == 'demo' else 'real')

    def _head(self, conn, oid):
        self._asset(conn, oid)
        row = conn.execute('SELECT version_id,revision FROM governance_heads WHERE ontology_id=?', (oid,)).fetchone()
        if row: return dict(row)
        last = _required(conn, 'SELECT version_id FROM versions WHERE ontology_id=? ORDER BY rowid DESC LIMIT 1', (oid,))
        return {'version_id': last['version_id'], 'revision': 0}

    def head(self, ontology_id):
        with self._atomic() as conn: return self._head(conn, ontology_id)

    def _cas(self, conn, oid, expected_head_version_id, expected_revision):
        if type(expected_revision) is not int or expected_revision < 0:
            raise GovernanceError('invalid_request', '修订号必须为非负整数。')
        head = self._head(conn, oid)
        if head != {'version_id': expected_head_version_id, 'revision': expected_revision}:
            raise GovernanceError('head_conflict', '工作头已经变化，请保留输入并比较新版本。')
        return head

    def _identity(self, args):
        for key in ('actor','reason','request_id'):
            authoring.text(args.get(key), key, 256 if key != 'reason' else 4096)

    def _retry(self, conn, oid, operation, args):
        self._asset(conn, oid)
        self._identity(args)
        fingerprint = _hash({'operation':operation, 'input':args})
        row = conn.execute('SELECT fingerprint,result FROM governance_requests WHERE ontology_id=? AND request_id=?',
                           (oid,args['request_id'])).fetchone()
        if row:
            if row['fingerprint'] != fingerprint:
                raise GovernanceError('request_conflict', '此请求 ID 已用于不同的输入。')
            return fingerprint, json.loads(row['result'])
        return fingerprint, None

    def _event(self, conn, oid, version_id, kind, args, **details):
        record = {'event_id':_uid('gov_'), 'ontology_id':oid, 'version_id':version_id, 'kind':kind,
                  'actor':args['actor'], 'reason':args['reason'], 'request_id':args['request_id'],
                  'created_at':evidence.utc_now(), **details}
        conn.execute('INSERT INTO governance_events VALUES (?,?,?,?)',
                     (record['event_id'],oid,version_id,json_text(record)))
        return record

    def _finish(self, conn, oid, fingerprint, args, result):
        conn.execute('INSERT INTO governance_requests VALUES (?,?,?,?)',
                     (oid,args['request_id'],fingerprint,json_text(result)))
        return result

    def _base(self, conn, oid, args):
        self._cas(conn, oid, args['expected_head_version_id'], args['expected_revision'])
        record = self._version(conn, oid, args['base_version_id'])
        if record['payload_sha256'] != args['base_payload_sha256']:
            raise GovernanceError('integrity_conflict', '基线摘要与登记值不一致。')
        return record, self._graph(conn, record)

    def _candidate(self, conn, oid, base, graph, args, *, sources=None, metadata=None, operation='save'):
        evidence.mark_governed(graph)
        info = deepcopy(base['metadata'])
        origin = info.pop('build_run_id', None)
        if origin: info['origin_build_run_id'] = origin
        info.update(evidence_policy=evidence.POLICY, mode=self._mode(base,graph))
        if origin:
            info['source_baseline'] = {key:base[key] for key in ('ontology_id','version_id','payload_sha256')}
        if metadata: info.update(metadata)
        saved = self.assets._add_version(conn, oid, graph.serialize(format='xml',encoding='utf-8'),
            payload_kind='robotics_rdf', parent_version_id=base['version_id'],
            source_asset_ids=base['source_asset_ids'] if sources is None else sources, metadata=info)
        revision = args['expected_revision'] + 1
        conn.execute('INSERT INTO governance_heads VALUES (?,?,?) ON CONFLICT(ontology_id) '
                     'DO UPDATE SET version_id=excluded.version_id,revision=excluded.revision', (oid,saved['version_id'],revision))
        details = {key:args[key] for key in ('operations','entity_iris','evidence_iri','decision') if key in args}
        if operation == 'register_evidence': details['evidence_iri'] = args['evidence']['iri']
        self._event(conn,oid,saved['version_id'],operation,args,base_version_id=base['version_id'],
                    base_payload_sha256=base['payload_sha256'],payload_sha256=saved['payload_sha256'],
                    revision=revision,**details)
        return saved

    def _edit(self, oid, operation, args, transform):
        common = {'base_version_id','base_payload_sha256','expected_head_version_id','expected_revision',
                  'request_id','actor','reason'}
        extra = {'save':{'operations'},'register_evidence':{'evidence'},
                 'review_evidence':{'evidence_iri','decision'},'review_knowledge':{'entity_iris','decision'}}
        if set(args) != common | extra[operation]:
            raise GovernanceError('invalid_request','条件写入字段不完整或包含不支持的字段。')
        with self._atomic(write=True) as conn:
            fingerprint, replay = self._retry(conn,oid,operation,args)
            if replay is not None: return replay
            base, graph = self._base(conn,oid,args)
            changed, sources = transform(conn,base,graph)
            result = self._candidate(conn,oid,base,changed,args,sources=sources,operation=operation)
            return self._finish(conn,oid,fingerprint,args,result)

    def save(self, ontology_id, *, base_version_id, base_payload_sha256, expected_head_version_id,
             expected_revision, request_id, actor, reason, operations):
        args = {key:value for key,value in locals().items() if key not in ('self','ontology_id')}
        return self._edit(ontology_id,'save',args,
                          lambda conn,base,graph: (authoring.apply_operations(graph,operations),base['source_asset_ids']))

    def register_evidence(self, ontology_id, *, evidence: dict, **args):
        declaration = deepcopy(evidence)
        call_args = {**args,'evidence':declaration}
        def register(conn,base,graph):
            source_ids = declaration.get('source_asset_ids', [])
            if not isinstance(source_ids,list): raise ValueError('附件需要逻辑 ID 列表。')
            rows = self._sources(conn,source_ids)
            from domains.robotics.evidence import register_evidence
            changed = register_evidence(graph,declaration,mode=self._mode(base,graph))
            subject = authoring.iri(declaration['iri'])
            # The digest, size and role are embedded in the reviewed evidence itself.
            for row in rows:
                if row['role'] != 'evidence': raise ValueError('证据附件角色不合法。')
                descriptor = {k:row[k] for k in ('source_asset_id','sha256','byte_size','role','media_type')}
                changed.add((subject,S.evidenceSourceSnapshot,Literal(json_text(descriptor),datatype=XSD.string)))
            return changed, list(dict.fromkeys(base['source_asset_ids'] + source_ids))
        return self._edit(ontology_id,'register_evidence',call_args,register)

    def review_evidence(self, ontology_id, *, evidence_iri, decision, **args):
        call_args = {**args,'evidence_iri':evidence_iri,'decision':decision}
        def approve(conn,base,graph):
            self._check_evidence_attachments(conn,graph,authoring.iri(evidence_iri))
            changed = evidence.review_evidence(graph,evidence_iri,decision=decision,actor=args['actor'],reason=args['reason'])
            if decision == 'accepted' and any(e['evidence_iri'] == evidence_iri for e in self._revocations(conn,ontology_id)):
                raise ValueError('已撤销的证据不能通过再次审核恢复；请登记新依据。')
            return changed, base['source_asset_ids']
        return self._edit(ontology_id,'review_evidence',call_args,approve)

    def _check_evidence_attachments(self, conn, graph, subject):
        ids = [str(raw) for raw in graph.objects(subject,S.evidenceSourceId)]
        actual = {row['source_asset_id']:row for row in self._sources(conn,ids)}
        snapshots = [json.loads(str(raw)) for raw in graph.objects(subject,S.evidenceSourceSnapshot)]
        if len(snapshots) != len(ids): raise ValueError('证据附件缺少固定摘要。')
        for snapshot in snapshots:
            row = actual.get(snapshot.get('source_asset_id'))
            if row is None or any(row.get(k) != v for k,v in snapshot.items()):
                raise GovernanceError('integrity_conflict','证据附件与审核范围不一致。')

    def review_knowledge(self, ontology_id, *, entity_iris, decision, **args):
        call_args = {**args,'entity_iris':entity_iris,'decision':decision}
        def approve(conn,base,graph):
            if decision not in ('accepted','rejected') or not isinstance(entity_iris,list) or not 1 <= len(entity_iris) <= 128:
                raise ValueError('知识审核需要明确对象集合与结论。')
            changed = deepcopy(graph)
            for raw in entity_iris:
                subject = authoring.iri(raw)
                authoring.kind(graph,subject)
                basis = [p for p in authoring.inspect_knowledge(graph)['provenance'] if p['iri'] == raw and p['basis']]
                if not basis: raise ValueError('需先登记该对象的知识及依据，再明确审核。')
                status = ('simulated' if self._mode(base,graph) == 'demo' else 'confirmed') if decision == 'accepted' else 'rejected'
                changed.set((subject,S.confirmationStatus,Literal(status,datatype=XSD.string)))
                authoring.provenance(changed,subject,'review',args['reason'])
            return changed,base['source_asset_ids']
        return self._edit(ontology_id,'review_knowledge',call_args,approve)

    def history(self, ontology_id):
        with self._atomic() as conn:
            self._asset(conn,ontology_id)
            return [json.loads(row[0]) for row in conn.execute('SELECT record FROM governance_events WHERE ontology_id=? ORDER BY rowid',(ontology_id,))]

    def visualize(self, ontology_id, version_id, view):
        """DR-068: both projections share the exact immutable, verified RDF version."""
        if view not in {'relations', 'behavior-tree'}:
            raise GovernanceError('invalid_request', '未知视图。')
        with self._atomic() as conn:
            version = self._version(conn, ontology_id, version_id)
            graph = self._graph(conn, version)
            try:
                projection = views.relations(graph) if view == 'relations' else views.behavior_tree(graph)
            except ValueError as exc:
                raise GovernanceError('view_unavailable', str(exc)) from exc
            return {'ontology_id': ontology_id, 'version_id': version_id,
                    'version_number': self.assets._version_number(conn, ontology_id, version_id),
                    'name': self._asset(conn, ontology_id)['name'], 'view': view, **projection}

    def inspect(self, ontology_id, version_id):
        """DR-059/061: project one RDF snapshot and current governance in one read transaction."""
        with self._atomic() as conn:
            version = self._version(conn, ontology_id, version_id)
            graph = self._graph(conn, version)
            revoked = self._revocations(conn, ontology_id)
            outcomes = []
            for subject in sorted(graph.subjects(RDF.type, S.ReadinessEvidence), key=str):
                self._check_evidence_attachments(conn, graph, subject)
                declaration = {key: str(graph.value(subject, predicate) or '') for key, predicate in (
                    ('kind', S.evidenceKind), ('implementation', S.forImplementation),
                    ('part_type', S.forPartType), ('reference', S.evidenceReference), ('valid_until', S.validUntil))}
                slot = graph.value(subject, S.forSlot)
                declaration.update(iri=str(subject), slot=str(slot) if slot is not None else None,
                                   source_asset_ids=[str(v) for v in graph.objects(subject, S.evidenceSourceId)])
                outcomes.append({'evidence_iri': str(subject),
                    'declaration': declaration,
                    'confirmation_status': str(graph.value(subject, S.confirmationStatus) or 'unknown'),
                    **evidence.evaluate_evidence(graph, str(subject), revocations=revoked)})
            history = [json.loads(r[0]) for r in conn.execute(
                'SELECT record FROM governance_events WHERE ontology_id=? ORDER BY rowid', (ontology_id,))]
            reviews = [json.loads(r[0]) for r in conn.execute(
                'SELECT record FROM governance_reviews WHERE ontology_id=? AND version_id=? ORDER BY rowid',
                (ontology_id, version_id))]
            return {'ontology_id': ontology_id, 'version_id': version_id,
                    'payload_sha256': version['payload_sha256'], 'head': self._head(conn, ontology_id),
                    'knowledge': authoring.inspect_knowledge(graph), 'evidence_policy': evidence.policy(graph) or 'legacy',
                    'evidence': outcomes, 'revocations': revoked, 'history': history, 'reviews': reviews,
                    'task_readiness': 'not_assessed', 'execution_readiness': 'not_verified'}

    def diff(self, ontology_id, base_version_id, target_version_id):
        with self._atomic() as conn:
            base = self._version(conn, ontology_id, base_version_id)
            target = self._version(conn, ontology_id, target_version_id)
            fields = ('ontology_id', 'version_id', 'payload_sha256')
            return {'base': {k: base[k] for k in fields}, 'target': {k: target[k] for k in fields},
                    'changes': version_diff.diff_graphs(self._graph(conn, base), self._graph(conn, target))}

    def register_source(self, ontology_id, content, media_type):
        """Register inert evidence bytes only after checking the target asset in the same transaction."""
        with self._atomic(write=True) as conn:
            self._asset(conn, ontology_id)
            return self.assets._put_source(conn, content, role='evidence', media_type=media_type)

    def read_payload(self, ontology_id, version_id):
        with self._atomic() as conn:
            return self._bytes(self._version(conn, ontology_id, version_id))

    def _report(self, conn, oid, version, graph):
        mode = self._mode(version,graph)
        validation, _ = consumer.validate_graph(graph,mode=mode)
        validation['ontology_sha256'] = version['payload_sha256']
        if evidence.policy(graph): validation['evidence_policy'] = evidence.POLICY
        projection = authoring.inspect_knowledge(graph)
        revocations = self._revocations(conn,oid)
        outcomes = []
        for subject in sorted(graph.subjects(RDF.type,S.ReadinessEvidence),key=str):
            if list(graph.objects(subject,S.evidenceSourceId)): self._check_evidence_attachments(conn,graph,subject)
            outcomes.append({'evidence_iri':str(subject), **evidence.evaluate_evidence(graph,str(subject),revocations=revocations)})
        return {'ontology_id':oid,'version_id':version['version_id'],'payload_sha256':version['payload_sha256'],
                'validation':validation,'knowledge_issues':projection['issues'],'evidence':outcomes,
                'evidence_policy':evidence.policy(graph) or 'legacy',
                'task_readiness':'not_assessed','execution_readiness':'not_verified'}

    def review(self, ontology_id, version_id, *, payload_sha256, decision, actor, reason, request_id):
        args = {key:value for key,value in locals().items() if key not in ('self','ontology_id')}
        with self._atomic(write=True) as conn:
            fingerprint,replay = self._retry(conn,ontology_id,'review',args)
            if replay is not None: return replay
            if decision not in ('accepted','rejected'): raise ValueError('审核结论不合法。')
            version = self._version(conn,ontology_id,version_id)
            if version['payload_sha256'] != payload_sha256: raise GovernanceError('integrity_conflict','审核摘要不一致。')
            report = self._report(conn,ontology_id,version,self._graph(conn,version))
            record = {'review_id':_uid('review_'),'ontology_id':ontology_id,'version_id':version_id,
                      'payload_sha256':payload_sha256,'decision':decision,'actor':actor,'reason':reason,
                      'created_at':evidence.utc_now(),'report_sha256':_hash(report),'report':report}
            conn.execute('INSERT INTO governance_reviews VALUES (?,?,?,?)',(record['review_id'],ontology_id,version_id,json_text(record)))
            self._event(conn,ontology_id,version_id,'review',args,review_id=record['review_id'],decision=decision)
            return self._finish(conn,ontology_id,fingerprint,args,record)

    def _artifact(self, conn, oid, version, kind, content, review_id):
        aid = _uid('vart_')
        relative = f'version_artifacts/{aid}.owl' if kind == 'ontology' else f'version_artifacts/{aid}.json'
        self.storage.write_bytes(relative,content)
        record = {'artifact_id':aid,'ontology_id':oid,'version_id':version['version_id'],
                  'payload_sha256':version['payload_sha256'],'review_id':review_id,'kind':kind,
                  'path':relative,'sha256':digest(content),'byte_size':len(content),
                  'media_type':'application/rdf+xml' if kind == 'ontology' else 'application/json',
                  'created_at':evidence.utc_now()}
        conn.execute('INSERT INTO version_artifacts VALUES (?,?,?,?,?)',(aid,oid,version['version_id'],kind,json_text(record)))
        return record

    def publish(self, ontology_id, *, version_id, payload_sha256, review_id, expected_head_version_id,
                expected_revision, request_id, actor, reason):
        args = {key:value for key,value in locals().items() if key not in ('self','ontology_id')}
        with self._atomic(write=True) as conn:
            fingerprint,replay = self._retry(conn,ontology_id,'publish',args)
            if replay is not None: return replay
            self._cas(conn,ontology_id,expected_head_version_id,expected_revision)
            if expected_head_version_id != version_id: raise GovernanceError('head_conflict','只能发布当前工作头。')
            version = self._version(conn,ontology_id,version_id)
            if version['payload_sha256'] != payload_sha256: raise GovernanceError('integrity_conflict','发布摘要不一致。')
            row = conn.execute('SELECT record FROM governance_reviews WHERE ontology_id=? AND version_id=? AND review_id=?',
                               (ontology_id,version_id,review_id)).fetchone()
            reviewed = json.loads(row[0]) if row else None
            if reviewed is None or reviewed['decision'] != 'accepted' or reviewed['payload_sha256'] != payload_sha256:
                raise GovernanceError('review_conflict','缺少针对该版本和摘要的接受审核。')
            graph = self._graph(conn,version)
            report = self._report(conn,ontology_id,version,graph)
            if reviewed['report_sha256'] != _hash(report):
                raise GovernanceError('review_conflict','审核后的结构或知识/证据结论发生变化，请重新审核。')
            existing = [json.loads(row[0]) for row in conn.execute('SELECT record FROM version_artifacts WHERE version_id=?',(version_id,))]
            if existing:
                if version['publication_state'] != 'published' or {r['kind'] for r in existing} != {'ontology','validation','manifest'}:
                    raise GovernanceError('integrity_conflict','版本工件登记不完整。')
                for item in existing: self.storage.read_bytes(item['path'],item['sha256'])
            else:
                owl = self._artifact(conn,ontology_id,version,'ontology',self._bytes(version),review_id)
                validation = self._artifact(conn,ontology_id,version,'validation',json_text({**report,'review_id':review_id}).encode(),review_id)
                manifest = {'ontology_id':ontology_id,'version_id':version_id,'payload_sha256':payload_sha256,
                            'review_id':review_id,'artifacts':[owl,validation], 'evidence_policy':report['evidence_policy'],
                            'source_asset_ids':version['source_asset_ids'],'execution_readiness':'not_verified'}
                self._artifact(conn,ontology_id,version,'manifest',json_text(manifest).encode(),review_id)
            conn.execute("UPDATE versions SET publication_state='published',publication_mode=?,validation=? WHERE version_id=?",
                         (self._mode(version,graph),json_text(report['validation']),version_id))
            self._event(conn,ontology_id,version_id,'publish',args,review_id=review_id)
            result = self._version(conn,ontology_id,version_id)
            return self._finish(conn,ontology_id,fingerprint,args,result)

    def artifacts(self, ontology_id, version_id):
        with self._atomic() as conn:
            self._version(conn,ontology_id,version_id)
            return [json.loads(row[0]) for row in conn.execute('SELECT record FROM version_artifacts WHERE ontology_id=? AND version_id=? ORDER BY rowid',
                                                             (ontology_id,version_id))]

    def read_artifact(self, ontology_id, version_id, artifact_id):
        with self._atomic() as conn:
            version = self._version(conn,ontology_id,version_id)
            row = _required(conn,'SELECT record FROM version_artifacts WHERE ontology_id=? AND version_id=? AND artifact_id=?',
                            (ontology_id,version_id,artifact_id))
            item = json.loads(row['record'])
            if item['payload_sha256'] != version['payload_sha256']: raise ValueError('工件不属于当前固定载荷。')
            raw = self.storage.read_bytes(item['path'],item['sha256'])
            if len(raw) != item['byte_size']: raise ValueError('工件长度不一致。')
            return raw

    def _revocations(self, conn, oid):
        return [json.loads(row[0]) for row in conn.execute('SELECT record FROM evidence_revocations WHERE ontology_id=? ORDER BY rowid',(oid,))]

    def revocations(self, ontology_id):
        with self._atomic() as conn:
            self._asset(conn,ontology_id)
            return self._revocations(conn,ontology_id)

    def revoke_evidence(self, ontology_id, *, evidence_iri, version_id, actor, reason, request_id, kind='revoked'):
        args = {key:value for key,value in locals().items() if key not in ('self','ontology_id')}
        with self._atomic(write=True) as conn:
            fingerprint,replay = self._retry(conn,ontology_id,'revoke_evidence',args)
            if replay is not None: return replay
            if kind not in ('revoked','superseded'): raise ValueError('未知失效类型。')
            version = self._version(conn,ontology_id,version_id)
            graph = self._graph(conn,version)
            if (authoring.iri(evidence_iri),RDF.type,S.ReadinessEvidence) not in graph: raise ValueError('该版本未登记此证据。')
            record = {'revocation_id':_uid('revoke_'),'ontology_id':ontology_id,'version_id':version_id,
                      'evidence_iri':evidence_iri,'kind':kind,'actor':actor,'reason':reason,'created_at':evidence.utc_now()}
            conn.execute('INSERT INTO evidence_revocations VALUES (?,?,?,?,?)',
                         (record['revocation_id'],ontology_id,version_id,evidence_iri,json_text(record)))
            self._event(conn,ontology_id,version_id,kind,args,evidence_iri=evidence_iri)
            return self._finish(conn,ontology_id,fingerprint,args,record)

    def _pinned(self, conn, ref):
        if not isinstance(ref,dict) or set(ref) != {'ontology_id','version_id','payload_sha256'}:
            raise ValueError('合并输入需要固定资产、版本和摘要。')
        version = self._version(conn,ref['ontology_id'],ref['version_id'])
        if version['payload_sha256'] != ref['payload_sha256']: raise GovernanceError('integrity_conflict','合并输入摘要变化。')
        return version,self._graph(conn,version)

    def _source_build(self, conn, version):
        rid = version['metadata'].get('build_run_id')
        row = conn.execute('SELECT * FROM runs WHERE run_id=?',(rid,)).fetchone() if rid else None
        if row is None or row['status'] != 'completed' or row['operation'] != 'build_ontology':
            raise GovernanceError('merge_conflict','缺少成功的来源构建基线，不推测来源。')
        output = json.loads(row['output_asset_ref']) if row['output_asset_ref'] else None
        build = json.loads(row['build_input']) if row['build_input'] else None
        if output != {k:version[k] for k in ('ontology_id','version_id')} or not build:
            raise GovernanceError('merge_conflict','来源构建未产出此固定版本。')
        sources = self._sources(conn,version['source_asset_ids'])
        if len(sources) != len(build['sources']) or any(
            any(actual[k] != expected[k] for k in ('source_asset_id','role','sha256','byte_size'))
            for actual,expected in zip(sources,build['sources'])
        ): raise GovernanceError('integrity_conflict','来源快照不一致。')
        if build['profile_id'] != 'ur_pick_place': raise GovernanceError('merge_conflict','不受支持的构建模板。')
        artifact = conn.execute("SELECT * FROM run_artifacts WHERE run_id=? AND kind='ontology'",(rid,)).fetchone()
        if artifact is None or artifact['sha256'] != version['payload_sha256'] or artifact['byte_size'] != version['byte_size']:
            raise GovernanceError('integrity_conflict','版本不是原构建登记的实际 OWL 字节。')
        self.storage.read_bytes(artifact['path'],artifact['sha256'])
        return row['run_id']

    def _merge(self, conn, oid, inputs, decisions):
        if not isinstance(inputs,dict) or set(inputs) != {'base','current','incoming','expected_head_version_id','expected_revision'}:
            raise ValueError('合并输入字段不完整。')
        self._cas(conn,oid,inputs['expected_head_version_id'],inputs['expected_revision'])
        base,bg = self._pinned(conn,inputs['base'])
        current,lg = self._pinned(conn,inputs['current'])
        incoming,rg = self._pinned(conn,inputs['incoming'])
        if current['ontology_id'] != oid or current['version_id'] != inputs['expected_head_version_id']:
            raise GovernanceError('merge_conflict','人工版本必须属于目标资产与当前工作头。')
        baseline_run = self._source_build(conn,base)
        incoming_run = self._source_build(conn,incoming)
        cursor, visited = current, set()
        while cursor['version_id'] != base['version_id']:
            # A previous explicit merge may point to a source build in another asset.
            if cursor['metadata'].get('merge', {}).get('incoming') == inputs['base']:
                break
            if not cursor['parent_version_id'] or cursor['version_id'] in visited:
                raise GovernanceError('merge_conflict','人工版本没有可证明的原来源基线。')
            visited.add(cursor['version_id'])
            cursor = self._version(conn,oid,cursor['parent_version_id'])
        if len({self._mode(v,g) for v,g in [(base,bg),(current,lg),(incoming,rg)]}) != 1:
            raise GovernanceError('merge_conflict','合并模式不一致。')
        if any(v['metadata'].get('profile_id') != 'ur_pick_place' for v in (base,current,incoming)):
            raise GovernanceError('merge_conflict','合并构建模板不一致。')
        consumer.validate_graph(bg,mode=self._mode(base,bg))
        consumer.validate_graph(rg,mode=self._mode(incoming,rg))
        preview = version_diff.merge_graphs(bg,lg,rg,decisions={})
        token = _hash({'inputs':inputs,'conflicts':preview['conflicts'],'policy':evidence.POLICY})
        result = preview if not decisions else version_diff.merge_graphs(bg,lg,rg,decisions=decisions)
        return {'merge_token':token,'conflicts':result['conflicts'],'inputs':inputs},result['graph'],current,incoming,baseline_run,incoming_run

    def preview_merge(self, ontology_id, **inputs):
        with self._atomic() as conn:
            preview,_,_,_,_,_ = self._merge(conn,ontology_id,inputs,{})
            return preview

    def commit_merge(self, ontology_id, *, merge_token, inputs, decisions, request_id, actor, reason):
        args = {key:value for key,value in locals().items() if key not in ('self','ontology_id')}
        with self._atomic(write=True) as conn:
            fingerprint,replay = self._retry(conn,ontology_id,'merge',args)
            if replay is not None: return replay
            preview,graph,current,incoming,baseline_run,incoming_run = self._merge(conn,ontology_id,inputs,decisions)
            if merge_token != preview['merge_token'] or graph is None:
                raise GovernanceError('merge_conflict','预览过期、输入被修改或仍有未解决冲突。')
            consumer.validate_graph(graph,mode=self._mode(current,graph))
            metadata = {'merge':{'base':inputs['base'],'incoming':inputs['incoming'],'decisions':decisions,
                                'baseline_run_id':baseline_run,'incoming_run_id':incoming_run},
                        'source_baseline':inputs['incoming'],'origin_build_run_id':incoming_run}
            # Preserve registered evidence attachments in addition to the new source pair.
            old_sources = self._sources(conn,current['source_asset_ids'])
            sources = incoming['source_asset_ids'] + [s['source_asset_id'] for s in old_sources if s['role']=='evidence']
            saved = self._candidate(conn,ontology_id,current,graph,{**args,**inputs},sources=sources,metadata=metadata,operation='merge')
            return self._finish(conn,ontology_id,fingerprint,args,saved)
