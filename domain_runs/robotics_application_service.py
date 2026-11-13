"""Explicit role confirmation, durable adaptation and original-task return — DR-072."""
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import uuid

from domains.robotics.application_adaptation import resolve_roles, adapt, export_bundle
from domains.robotics.modeling.common import digest as json_digest
from ontology_assets import AssetStore
from ontology_assets.storage import Storage
from ontology_assets.payloads import json_text
from ontology_assets.robotics_design import DesignAssets
from ontology_assets.robotics_application import CONTRACT, save
from srv_context import _RDF_LOCK
from .store import RunStore, _run, _now


class ApplicationError(ValueError):
    def __init__(self, code, message, *, issues=None):
        super().__init__(message); self.code = code; self.issues = issues


def require(condition, code='invalid_request', message='请求或当前状态不符合应用适配要求。'):
    if not condition: raise ApplicationError(code, message)


def text(value): return isinstance(value,str) and bool(value.strip()) and len(value)<=128 and '\0' not in value


def design(root, ref):
    reader = DesignAssets(root)
    version, doc, files = reader._load(**ref)
    state = doc['state']
    return version, doc, {'tbox':state['schema_confirmation']['tbox'],
        'confirmation':state['schema_confirmation'], 'abox':doc['abox'], 'coverage':doc['coverage'],
        'evidence':state['evidence'], 'ontology':files['candidate.owl'],
        'origin':{**ref, 'payload_sha256':version['payload_sha256']}}


def proposal(tbox):
    # Only canonical business roles get suggestions; custom terms remain options.
    from domains.robotics._application_roles import CLASSES, RELATIONS, DATA
    from domains.robotics.graph import schema, S
    from rdflib import RDF, OWL
    standard = schema()
    known = {kind: {str(iri)[len(str(S)):] for iri in standard.subjects(RDF.type, type_)
                    if str(iri).startswith(str(S))}
        for kind, type_ in (('classes', OWL.Class), ('object_properties', OWL.ObjectProperty), ('data_properties', OWL.DatatypeProperty))}
    return {'version':'1','tbox_sha256':json_digest(tbox),'confirmed':False,
        'required_roles':{'classes':sorted(CLASSES),'object_properties':sorted(RELATIONS),'data_properties':sorted(DATA)},
        'bindings':[{'kind':k,'role':r['id'],'iri':tbox['namespace']+r['id']}
            for k in known for r in tbox[k] if r['id'] in known[k]],
        'term_options':[{'kind':k,'id':r['id'],'label':r['label'],'iri':tbox['namespace']+r['id']}
            for k in known for r in tbox[k]]}


def diagnosis(tbox, roles):
    return resolve_roles(tbox=tbox, role_map={**roles,'confirmed':True})['issues']


class ApplicationService:
    def __init__(self, root, *, read_only=False):
        self.root, self.read_only = Path(root), read_only
        if not read_only:
            self._storage()
            self.runs=RunStore(root)
            self.assets=AssetStore(root)

    def _storage(self):
        try: return Storage(self.root,read_only=self.read_only)
        except (ValueError,OSError,sqlite3.Error):
            raise ApplicationError('storage_unavailable','适配存储暂不可用。') from None

    @contextmanager
    def _atomic(self, *, write=False):
        require(not write or not self.read_only)
        with _RDF_LOCK, self._storage().transaction(write=write) as conn: yield conn

    def _get(self, conn, aid):
        require(text(aid))
        row = conn.execute('SELECT record FROM robotics_adaptations WHERE adaptation_id=?',(aid,)).fetchone()
        require(row is not None,'not_found','适配记录不存在。')
        return json.loads(row[0])

    def get(self, adaptation_id):
        require((self.root/'domain.sqlite3').is_file(),'not_found','适配记录不存在。')
        with self._atomic() as conn: return self._get(conn,adaptation_id)

    def _put(self, conn, state):
        conn.execute('INSERT INTO robotics_adaptations VALUES (?,?) ON CONFLICT(adaptation_id) DO UPDATE SET record=excluded.record',
                     (state['adaptation_id'],json_text(state)))

    def _request(self, conn, scope, rid, operation, args):
        require(text(rid)); fingerprint = json_digest({'operation':operation,**args})
        row=conn.execute('SELECT fingerprint,response FROM robotics_adaptation_requests WHERE scope=? AND request_id=?',(scope,rid)).fetchone()
        if row:
            require(row[0]==fingerprint,'request_conflict'); return json.loads(row[1]),fingerprint
        return None,fingerprint

    def _reply(self, conn, scope, rid, fingerprint, value):
        conn.execute('INSERT INTO robotics_adaptation_requests VALUES (?,?,?,?)',(scope,rid,fingerprint,json_text(value)))
        return deepcopy(value)

    def _revision(self, state, expected):
        require(type(expected) is int and expected>=0)
        require(expected==state['revision'],'revision_conflict','适配记录已变化，请刷新后重试。')

    def _head(self, conn, oid):
        last=conn.execute('SELECT version_id FROM versions WHERE ontology_id=? ORDER BY rowid DESC LIMIT 1',(oid,)).fetchone()
        require(last is not None,'not_found')
        row=conn.execute('SELECT version_id,revision FROM governance_heads WHERE ontology_id=?',(oid,)).fetchone()
        return {'latest':last[0],'governance':dict(row) if row else None}

    def _head_check(self, conn, state):
        require(self._head(conn,state['asset_ref']['ontology_id'])==state['head'],'head_conflict','本体已有新修订，请刷新并明确选择版本。')

    def _prepare(self, conn, *, ontology_id, version_id, mode, task_ref, request_id, automatic=False):
        require(type(automatic) is bool)
        ref={'ontology_id':ontology_id,'version_id':version_id}
        require(all(text(v) for v in ref.values()) and version_id!='latest' and mode in ('real','demo'))
        if task_ref is not None: require(isinstance(task_ref,dict) and set(task_ref)=={'session_id'} and text(task_ref['session_id']))
        args={**ref,'mode':mode,'task_ref':task_ref}
        if automatic:args['automatic']=True
        replay,fp=self._request(conn,'prepare',request_id,'prepare',args)
        if replay is not None: return replay
        require(mode=='real','mode_conflict','当前设计来源为真实模式，不能转换为演示本体。')
        _,doc,core=design(self.root,ref)
        task_revision=None
        if task_ref:
            row=conn.execute('SELECT record FROM robotics_task_sessions WHERE session_id=?',(task_ref['session_id'],)).fetchone()
            require(row is not None,'not_found'); task=json.loads(row[0])
            require(task['ontology_id']==ontology_id and task['mode']==mode,'mode_conflict')
            task_revision=task['revision']
        suggested=proposal(core['tbox']); issues=diagnosis(core['tbox'],suggested)
        state={'adaptation_id':'adapt_'+uuid.uuid4().hex,'asset_ref':ref,'mode':mode,'task_ref':task_ref,
            'task_revision':task_revision,'revision':0,'status':'needs_tbox_revision' if issues else 'awaiting_role_confirmation',
            'role_proposal':suggested,'role_map':None,'issues':issues,'run_id':None,'output_asset_ref':None,
            'head':self._head(conn,ontology_id),'origin_session':doc['session_ref'],'created_at':_now(),
            'automation_requested':automatic}
        if automatic:self._automatic(conn,state,doc,core)
        self._put(conn,state)
        return self._reply(conn,'prepare',request_id,fp,state)

    def prepare(self, **kwargs):
        with self._atomic(write=True) as conn: return self._prepare(conn,**kwargs)

    def _automatic(self, conn, state, doc, core):
        """Fixed-ID policy, validated semantics and a known frozen vocabulary only."""
        from domains.robotics.modeling.vocabulary import load_vocabulary
        frozen=doc['state']['vocabulary']['sha256']
        trusted=frozen==load_vocabulary()['sha256']
        required=state['role_proposal']['required_roles']
        problems=state['issues']
        unresolved=list({(i['kind'],i['role']):{'kind':i['kind'],'role':i['role']}
            for i in problems if i.get('kind') and i.get('role')}.values())
        if not trusted and not unresolved:
            unresolved=[{'kind':k,'role':r} for k,rows in required.items() for r in rows]
        state['matching']={'method':'manual','rule_version':'canonical-role-id-v1','vocabulary_sha256':frozen,
            'reason':'unresolved_roles' if problems else 'vocabulary_review_required' if not trusted else 'canonical_ids_validated',
            'unresolved':unresolved}
        if problems or not trusted:return
        self._set_roles(state,state['role_proposal']['bindings'],core,method='automatic')
        self._queue(conn,state,core=core)

    def _set_roles(self, state, bindings, core, *, method='manual'):
        require(isinstance(bindings,list) and all(isinstance(b,dict) and set(b)=={'kind','role','iri'}
            and all(isinstance(v,str) and bool(v) for v in b.values()) for b in bindings))
        role_map={'version':'1','tbox_sha256':json_digest(core['tbox']),'confirmed':True,'bindings':deepcopy(bindings)}
        result=resolve_roles(tbox=core['tbox'],role_map=role_map)
        if result['status']!='passed':
            raise ApplicationError('role_conflict','角色对应与确认骨架不一致，请核对类别、方向及属性类型。',issues=result['issues'])
        matching={**state.get('matching',{}),'method':method,
            'rule_version':'canonical-role-id-v1' if method=='automatic' else 'explicit-roles-v1',
            'reason':'canonical_ids_validated' if method=='automatic' else 'user_confirmed','unresolved':[]}
        state.update(role_map=role_map,status='roles_confirmed',issues=[],revision=state['revision']+1,matching=matching)

    def confirm_roles(self, adaptation_id, *, bindings, expected_revision, request_id):
        with self._atomic(write=True) as conn:
            state=self._get(conn,adaptation_id)
            replay,fp=self._request(conn,adaptation_id,request_id,'confirm',{'bindings':bindings,'revision':expected_revision})
            if replay is not None:return replay
            self._revision(state,expected_revision)
            require(state['status'] in ('awaiting_role_confirmation','needs_tbox_revision','roles_confirmed'))
            _,_,core=design(self.root,state['asset_ref'])
            self._set_roles(state,bindings,core)
            self._put(conn,state); return self._reply(conn,adaptation_id,request_id,fp,state)

    def _queue(self, conn, state, *, core=None):
        require(state['role_map'] is not None,'confirmation_required','请先确认角色对应。')
        require(state['status'] in ('roles_confirmed','failed','interrupted'))
        self._head_check(conn,state)
        if core is None:design(self.root,state['asset_ref'])
        # A repair handler lends its existing write transaction; no nested writer.
        runs=getattr(self,'runs',None) or RunStore(self.root,read_only=True)
        inputs={**state['asset_ref'],'operation':'adapt_execution','task':{},'mode':state['mode'],
            'session_ref':{'namespace':'robotics_adaptation','id':state['adaptation_id']},
            'components':{'profile_id':'ur_pick_place','model':{'invoked':False},'adapter_version':'ur-fixed-adaptation-1',
                'mapping_method':state.get('matching',{}).get('method','manual')}}
        run=runs._insert(conn,inputs,runs._snapshot(conn,**state['asset_ref']),retry_of=state['run_id'])
        state.update(status='queued',run_id=run['run_id'],output_asset_ref=None,revision=state['revision']+1)
        self._put(conn,state)

    def submit(self, adaptation_id, *, expected_revision, request_id, bindings=None):
        with self._atomic(write=True) as conn:
            state=self._get(conn,adaptation_id)
            args={'revision':expected_revision}
            if bindings is not None:args['bindings']=bindings
            replay,fp=self._request(conn,adaptation_id,request_id,'submit',args)
            if replay is not None:return replay
            self._revision(state,expected_revision)
            if bindings is not None:
                require(state['status'] in ('awaiting_role_confirmation','needs_tbox_revision','roles_confirmed'))
                _,_,core=design(self.root,state['asset_ref'])
                self._set_roles(state,bindings,core)
            self._queue(conn,state)
            return self._reply(conn,adaptation_id,request_id,fp,state)

    def claim_next(self):
        with self._atomic(write=True) as conn:
            row=conn.execute("SELECT run_id FROM runs WHERE operation='adapt_execution' AND status='queued' ORDER BY rowid LIMIT 1").fetchone()
            if not row:return None
            self.runs._state(conn,row[0],'running'); return _run(conn,row[0])

    def execute(self, run_id):
        runs=RunStore(self.root)
        assets=AssetStore(self.root)
        try:
            with self._atomic(write=True) as conn:
                run=_run(conn,run_id);require(run['operation']=='adapt_execution')
                if run['status'] in ('completed','failed','interrupted'):return run
                aid=run['session_ref']['id'];state=self._get(conn,aid)
                require(state['run_id']==run_id)
                if run['status']=='queued':runs._state(conn,run_id,'running')
                self._head_check(conn,state)
            _,doc,core=design(self.root,state['asset_ref'])
            product=adapt(design=core,role_map=state['role_map'],mode=state['mode'])
            if product['status']!='adapted':
                raise ApplicationError('execution_blocked','执行事实尚未满足适配要求。',issues=product['issues'])
            with self._atomic(write=True) as conn:
                current=self._get(conn,aid);require(current['run_id']==run_id and current['revision']==state['revision'],'revision_conflict')
                self._head_check(conn,state);runs._verify_snapshot(conn,_run(conn,run_id))
                design(self.root,state['asset_ref'])
                base=assets.get_version(**state['asset_ref'])
                version=assets._add_version(conn,base['ontology_id'],product['ontology'],payload_kind='robotics_rdf',
                    parent_version_id=base['version_id'],source_asset_ids=base['source_asset_ids'],
                    metadata={'application_contract':CONTRACT,'profile_id':'ur_pick_place','mode':state['mode'],
                        'build_run_id':run_id,'adaptation_id':aid,'origin_design_session':doc['session_ref']})
                ref={k:version[k] for k in ('ontology_id','version_id')}
                bundle=export_bundle(product,output_asset_ref=ref,parent_version_id=base['version_id'])
                document=json.loads(bundle['json']);document['origin']['messages']=deepcopy(doc['state'].get('messages',[]))
                document['components'].update(mapping_method=state.get('matching',{}).get('method','manual'),
                    matching_rule_version=state.get('matching',{}).get('rule_version','explicit-roles-v1'))
                document['origin'].update(design_components=doc['components'],session_ref=doc['session_ref'],
                    revision=doc['revision'],run_id=doc['run_id'])
                encoded=save(assets.storage,conn,version,document)
                current_run=_run(conn,run_id);current_run['output_asset_ref']=ref
                outcome={'status':'adapted','execution_readiness':'not_verified','issues':[]}
                runs._complete(conn,current_run,outcome,[{'name':'candidate.owl','kind':'ontology','media_type':'application/rdf+xml','content':bundle['owl']},
                    {'name':'design.json','kind':'design','media_type':'application/json','content':encoded}])
                head_revision=(state['head']['governance'] or {}).get('revision',0)
                conn.execute('INSERT INTO governance_heads VALUES (?,?,?) ON CONFLICT(ontology_id) '
                    'DO UPDATE SET version_id=excluded.version_id,revision=excluded.revision',
                    (ref['ontology_id'],ref['version_id'],head_revision+1))
                state.update(status='completed',output_asset_ref=ref,revision=state['revision']+1,issues=[])
                self._put(conn,state)
                return _run(conn,run_id)
        except (ValueError,OSError,KeyError,sqlite3.Error) as exc:
            code=getattr(exc,'code','execution.source_integrity')
            if code=='source_integrity':code='execution.source_integrity'
            if isinstance(exc,sqlite3.Error):code='system.save_failed'
            with self._atomic(write=True) as conn:
                run=_run(conn,run_id)
                if run['status']=='completed':return run
                state=self._get(conn,run['session_ref']['id'])
                problems=getattr(exc,'issues',None) or [{'code':code,'message':'适配未完成，请检查来源、保存状态或当前修订后显式重试。'}]
                outcome={'status':'error','issues':deepcopy(problems)}
                conn.execute("UPDATE runs SET status='failed',result=?,updated_at=? WHERE run_id=?",(json_text(outcome),_now(),run_id))
                runs._event(conn,run_id,'state',{'status':'failed'})
                state.update(status='failed',issues=outcome['issues'],output_asset_ref=None,revision=state['revision']+1)
                self._put(conn,state);return _run(conn,run_id)

    def rebase(self, adaptation_id, *, asset_ref, expected_revision, request_id):
        with self._atomic(write=True) as conn:
            state=self._get(conn,adaptation_id)
            replay,fp=self._request(conn,adaptation_id,request_id,'rebase',{'asset_ref':asset_ref,'revision':expected_revision})
            if replay is not None:return replay
            self._revision(state,expected_revision)
            require(state['status'] not in ('queued','running'))
            require(set(asset_ref)=={'ontology_id','version_id'} and asset_ref['ontology_id']==state['asset_ref']['ontology_id'])
            _,doc,core=design(self.root,asset_ref);suggested=proposal(core['tbox']);issues=diagnosis(core['tbox'],suggested)
            state.update(asset_ref=asset_ref,head=self._head(conn,asset_ref['ontology_id']),role_proposal=suggested,
                role_map=None,run_id=None,output_asset_ref=None,issues=issues,revision=state['revision']+1,
                status='needs_tbox_revision' if issues else 'awaiting_role_confirmation')
            if state.get('automation_requested'):self._automatic(conn,state,doc,core)
            self._put(conn,state);return self._reply(conn,adaptation_id,request_id,fp,state)

    def resume_task(self, adaptation_id, *, expected_revision, expected_task_revision, request_id):
        from .robotics_task_service import TaskService, _clear
        tasks=TaskService(self.root,generation_policy='offline')
        with self._atomic(write=True) as conn:
            state=self._get(conn,adaptation_id)
            replay,fp=self._request(conn,adaptation_id,request_id,'resume',{'revision':expected_revision,'task_revision':expected_task_revision})
            if replay is not None:return replay
            self._revision(state,expected_revision); require(state['status']=='completed' and state['task_ref'])
            task=tasks._get(conn,state['task_ref']['session_id'])
            require(type(expected_task_revision) is int and expected_task_revision==state['task_revision']==task['revision'],'session_conflict')
            task.update(**state['output_asset_ref']);_clear(task);task['revision']+=1
            task['version_number']=self.assets._version_number(conn,task['ontology_id'],task['version_id'])
            tasks._graph(task,conn);task['status']='needs_information' if task['task'] else 'idle'
            if task['task']:task['status']=tasks._project(task,tasks._graph(task,conn),tasks.governance._revocations(conn,task['ontology_id']))
            tasks._put(conn,task);return self._reply(conn,adaptation_id,request_id,fp,task)
