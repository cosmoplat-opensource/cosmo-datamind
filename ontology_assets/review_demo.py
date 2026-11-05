"""Fresh XML governance demonstrations and read-only independent verification — DR-060 / DR-061 / DR-064 / DR-065."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET

from rdflib import Graph

from domain_runs import RunStore
from domain_runs.build_service import BuildService
from domain_runs.robotics_build import compute_build
from domain_runs.robotics import generate_from_version
from domain_runs.verification import verify_storage
from domains.robotics.evidence import evaluate_evidence
from domains.robotics.graph import S
from ontology_assets.governance import GovernanceError, GovernanceService
from ontology_assets.payloads import json_text
from ontology_assets.storage import digest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/demo_robotics_review.py'
CASES = ('incomplete-publication', 'concurrent-edit', 'rebuild-conflict', 'tamper-detection')
TASK = {'operation':'pick_place', 'part':'零部件A', 'station':'工位B', 'carrier':'载具B01',
        'slot':'放置位B01', 'unsupported_requests':[]}


def require(condition, message):
    if not condition: raise ValueError(message)


def write(path, body):
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def identity():
    return {'request_id': 'demo-' + uuid.uuid4().hex, 'actor':'离线合成演示',
            'reason':'仅验证软件契约，不证明机器人现场适配'}


def edit_args(service, version):
    head = service.head(version['ontology_id'])
    return {**identity(), 'base_version_id':version['version_id'], 'base_payload_sha256':version['payload_sha256'],
            'expected_head_version_id':head['version_id'], 'expected_revision':head['revision']}


def field(name='label', value='离线维护的零件类型', iri=S.PartType_A):
    return {'op':'set_field', 'iri':str(iri), 'field':name, 'value':value, 'basis':'合成资料 TEST-ONLY，非现场确认'}


def build(root, name, source=None):
    directory = ROOT / 'tests/fixtures/robotics/sources'
    run = BuildService(root).submit(name=name, recipe='bt_xml', profile_id='ur_pick_place', request_id=uuid.uuid4().hex,
        sources={'behavior_tree':{'filename':'source.xml','content':source if source is not None else (directory / 'xingweishu.xml').read_bytes()},
                 'node_models':{'filename':'models.xml','content':(directory / 'xml_models.xml').read_bytes()}})
    runs = RunStore(root)
    runs.start(run['run_id'])
    result = runs.finish_build(run['run_id'], **compute_build(root, run['run_id']))
    require(result['status'] == 'completed' and result['result']['status'] == 'built', '双 XML 实际构建失败')
    return GovernanceService(root).assets.get_version(**result['output_asset_ref'])


def publish(service, version):
    oid, vid = version['ontology_id'], version['version_id']
    reviewed = service.review(oid, vid, payload_sha256=version['payload_sha256'], decision='accepted', **identity())
    head = service.head(oid)
    published = service.publish(oid, version_id=vid, payload_sha256=version['payload_sha256'], review_id=reviewed['review_id'],
                                expected_head_version_id=head['version_id'], expected_revision=head['revision'], **identity())
    return published, reviewed


def child_verify(directory):
    return subprocess.run([sys.executable, str(SCRIPT), '--verify', str(directory)], capture_output=True, text=True, timeout=120)


def verify_output(directory):
    """Enumerate durable records, never initialize/recover or trust summary's success flag."""
    directory = Path(directory).absolute()
    summary = json.loads((directory / 'summary.json').read_text())
    require(summary.get('schema_version') == 1 and summary.get('storage_root') == 'storage', '演示汇总格式不合法')
    root = directory / 'storage'
    verify_storage(root)
    service = GovernanceService(root, read_only=True)
    versions = []
    # Reviews are immutable historical reports; verify their own digest rather than re-evaluating today's expiry.
    with service.storage.transaction() as conn:
        reviews = [json.loads(r[0]) for r in conn.execute('SELECT record FROM governance_reviews')]
        for r in reviews:
            version = service.assets.get_version(r['ontology_id'], r['version_id'])
            require(r['payload_sha256'] == version['payload_sha256'], '审核摘要与版本不一致')
            require(r['report_sha256'] == digest(json_text(r['report']).encode()), '审核报告摘要不一致')
            require(all(r['report'][key] == r[key] for key in ('ontology_id','version_id','payload_sha256')), '审核报告身份不一致')
            require(r['decision'] in ('accepted','rejected') and r['actor'] and r['reason'], '审核记录不完整')
    review_map = {r['review_id']:r for r in reviews}
    for asset in service.assets.list_assets():
        oid = asset['ontology_id']
        for v in service.assets.list_versions(oid):
            vid = v['version_id']
            raw = service.read_payload(oid, vid)
            require(digest(raw) == v['payload_sha256'] and len(raw) == v['byte_size'], '版本载荷校验失败')
            if v['parent_version_id']: service.assets.get_version(oid,v['parent_version_id'])
            records = service.artifacts(oid,vid)
            if v['publication_state'] == 'published':
                require({r['kind'] for r in records} == {'ontology','validation','manifest'} and len(records) == 3, '发布工件不完整')
            elif records: raise ValueError('未发布版本出现发布工件')
            for record in records:
                content = service.read_artifact(oid,vid,record['artifact_id'])
                require(len(content) == record['byte_size'] and digest(content) == record['sha256'], '工件登记与实际字节不一致')
                reviewed = review_map.get(record['review_id'])
                require(reviewed and reviewed['decision'] == 'accepted' and reviewed['version_id'] == vid
                        and reviewed['ontology_id'] == oid and reviewed['payload_sha256'] == v['payload_sha256'], '工件引用无效审核')
                if record['kind'] == 'ontology': require(content == raw, '下载 OWL 不是本版本')
                else:
                    document = json.loads(content)
                    require(all(document[k] == record[k] for k in ('ontology_id','version_id','payload_sha256','review_id')), '工件清单身份不一致')
                    require(document['execution_readiness'] == 'not_verified', '演示不得宣称执行通过')
                    if record['kind'] == 'validation':
                        require({k:v for k,v in document.items() if k != 'review_id'} == reviewed['report'], '发布报告与审核报告不一致')
                    else:
                        expected = [r for r in records if r['kind'] != 'manifest']
                        require(document['artifacts'] == expected and document['source_asset_ids'] == v['source_asset_ids'], '清单与登记工件/来源不一致')
            versions.append({k:v[k] for k in ('ontology_id','version_id','payload_sha256')})
    require(summary.get('cases') and all(r.get('case') in CASES for r in summary['cases']), '汇总场景缺失或未知')
    for row in summary['cases']:
        v = service.assets.get_version(row['ontology_id'],row['version_id'])
        base = service.assets.get_version(row['ontology_id'],row['base_version_id'])
        run = RunStore(root,read_only=True).get_run(base['metadata']['build_run_id'])
        require(run['output_asset_ref'] == {k:base[k] for k in ('ontology_id','version_id')}, '原始构建引用不一致')
        require(v['version_id'] != base['version_id'], '演示缺少实际新版本')
        if row['case'] == 'incomplete-publication':
            runs = RunStore(root,read_only=True)
            result = runs.get_run(row['run_id'])
            require(v['publication_state'] == 'published' and result['mode'] == 'real'
                    and result['version_id'] == v['version_id'] and result['result']['status'] == 'needs_information'
                    and result['result']['issues'] and not any(a['kind'] == 'candidate' for a in runs.artifacts(row['run_id'])),
                    '缺证据应阻断严格任务生成')
        if row['case'] == 'concurrent-edit':
            require(row['loser_error'] == 'head_conflict' and len(service.assets.list_versions(row['ontology_id'])) == 2, '并发场景结果不正确')
        if row['case'] == 'rebuild-conflict':
            require(set(row['conflict_ids']) == set(v['metadata']['merge']['decisions']), '合并选择与登记不一致')
            graph = Graph().parse(data=service.assets.read_payload(row['ontology_id'],v['version_id']),format='xml')
            require(not evaluate_evidence(graph,row['evidence_iri'],revocations=service.revocations(row['ontology_id']))['valid'], '合并后旧证据不应有效')
            require(str(graph.value(S.Perception_Current,S.threshold)) == '0.71', '合并没有保留所选人工值')
        if row['case'] == 'tamper-detection':
            require(row.get('tamper_probe') == 'tamper-probe' and row.get('probe_exit_code') != 0, '缺少损坏探针记录')
            probe = GovernanceService(directory / 'tamper-probe/storage',read_only=True)
            try: probe.read_artifact(row['ontology_id'],row['version_id'],row['damaged_artifact_id'])
            except GovernanceError as exc: require(exc.code in {'integrity_conflict','invalid_request'},'损坏探针错误类型不匹配')
            else: raise ValueError('损坏探针的工件未损坏')
    return {'passed':True,'pid':os.getpid(),'verified_versions':versions}


def run_demo(case, output_dir):
    require(case in (*CASES,'all'), '未知演示场景')
    output = Path(output_dir).absolute()
    require(not output.exists() or output.is_dir() and not any(output.iterdir()), '输出目录非空，拒绝覆盖')
    output.mkdir(parents=True,exist_ok=True)
    root = output / 'storage'
    summary = {'schema_version':1,'storage_root':'storage','producer_pid':os.getpid(),'passed':False,'cases':[]}
    for name in CASES if case == 'all' else [case]:
        base = build(root,name)
        service = GovernanceService(root)
        oid = base['ontology_id']
        original = service.assets.read_payload(oid,base['version_id'])
        row = {'case':name,'ontology_id':oid,'base_version_id':base['version_id'],'passed':False}
        if name == 'concurrent-edit':
            args = edit_args(service,base)
            v = service.save(oid,operations=[field()],**args)
            try: service.save(oid,operations=[field(value='第二编辑者')],**{**args,**identity()})
            except GovernanceError as exc:
                require(exc.code == 'head_conflict', '错误的冲突类型'); row['loser_error'] = exc.code
            else: raise ValueError('过期编辑意外成功')
        elif name == 'rebuild-conflict':
            mapping = {'op':'set_relation','iri':str(S.PartType_A),'relation':'hasPerceptionProfile',
                       'targets':[str(S.Perception_Current)],'basis':'合成范围映射 TEST-ONLY'}
            current = service.save(oid,operations=[mapping],**edit_args(service,base))
            evidence_iri = str(S.OfflineReviewEvidence)
            spec = {'iri':evidence_iri,'kind':'grasp_compatibility','implementation':str(S.grasp_Subtree_Impl),
                    'part_type':str(S.PartType_A),'slot':None,'reference':'合成依据 TEST-ONLY，不证明现场能力',
                    'source_asset_ids':[],'valid_until':'2999-01-01T00:00:00+00:00'}
            current = service.register_evidence(oid,evidence=spec,**edit_args(service,current))
            current = service.review_evidence(oid,evidence_iri=evidence_iri,decision='accepted',**edit_args(service,current))
            current = service.save(oid,operations=[field('threshold','0.71',S.Perception_Current)],**edit_args(service,current))
            tree = ET.fromstring((ROOT / 'tests/fixtures/robotics/sources/xingweishu.xml').read_bytes())
            tree.find("BehaviorTree[@ID='main']/Sequence")[4].set('threshold','0.73')
            incoming = build(root,'重建来源',ET.tostring(tree))
            ref = lambda v: {k:v[k] for k in ('ontology_id','version_id','payload_sha256')}
            head = service.head(oid)
            inputs = {'base':ref(base),'current':ref(current),'incoming':ref(incoming),
                      'expected_head_version_id':head['version_id'],'expected_revision':head['revision']}
            preview = service.preview_merge(oid,**inputs)
            require(preview['conflicts'], '重建演示未产生预期冲突')
            decisions = {c['id']:{'choice':'invalidate' if c['kind'] == 'evidence_scope' else 'current'} for c in preview['conflicts']}
            v = service.commit_merge(oid,merge_token=preview['merge_token'],inputs=inputs,decisions=decisions,**identity())
            row.update(conflict_ids=list(decisions),evidence_iri=evidence_iri,evidence_review_required=True)
        else:
            current = service.save(oid,operations=[field()],**edit_args(service,base))
            v, reviewed = publish(service,current)
            row['review_id'] = reviewed['review_id']
            if name == 'incomplete-publication':
                run = generate_from_version(root,ontology_id=oid,version_id=v['version_id'],task=TASK,mode='real',
                                            session_ref={'namespace':'review_demo','id':name})
                require(run['result']['status'] == 'needs_information','缺依据的任务意外成功')
                row['run_id'] = run['run_id']
        row.update(version_id=v['version_id'],passed=True)
        require(service.assets.read_payload(oid,base['version_id']) == original,'原版本被改写')
        summary['cases'].append(row)
        if name == 'tamper-detection':
            probe = output / 'tamper-probe'
            probe.mkdir(); shutil.copytree(root,probe / 'storage')
            record = next(r for r in service.artifacts(oid,v['version_id']) if r['kind'] == 'ontology')
            (probe / 'storage' / record['path']).write_bytes(b'intentionally corrupted offline probe')
            write(probe / 'summary.json',summary)
            process = child_verify(probe)
            require(process.returncode != 0,'损坏探针没有被拒绝')
            row.update(tamper_probe='tamper-probe',probe_exit_code=process.returncode,damaged_artifact_id=record['artifact_id'])
    write(output / 'summary.json',summary)
    process = child_verify(output)
    if process.returncode: raise ValueError('独立核验失败：' + process.stderr[-1500:])
    summary['verification'] = json.loads(process.stdout)
    summary['passed'] = all(r['passed'] for r in summary['cases']) and summary['verification']['passed']
    write(output / 'summary.json',summary)
    lines = ['# 机器人知识治理离线演示','','来源为冻结的双 XML；所有依据为合成资料，不证明现场适配。',
             '结构发布不等于任务适配，执行状态始终未验证。','','| 场景 | 结果 | 固定版本 |','|---|---|---|']
    for row in summary['cases']: lines.append(f"| {row['case']} | PASS | {row['version_id']} |")
    lines += ['', '独立进程核验：PASS', '', '实际 OWL、来源、运行和审核工件保存在 storage/；可使用 --verify 再次只读核验。']
    (output / 'summary.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return summary
