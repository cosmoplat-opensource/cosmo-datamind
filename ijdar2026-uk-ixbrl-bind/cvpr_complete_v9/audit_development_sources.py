"""Read-only publisher transform audit on the already reserved 14-source development cohort.

These publisher annotations are for teacher/scorer preparation. Browser-visible
geometry, header ownership and complete semantic bindings remain uncertified.
No calibration or held-out source is accessed, and no task is selected by scores.
"""
from collections import Counter
import json
from pathlib import Path
import sys

from prepare import EXP, OUT, save, sha


def main():
    source_root=EXP/'runs/visual_scope_relations_v8_20261009/scope_boundary_development_v1'
    plan=json.loads((source_root/'download_plan.json').read_text())
    status=json.loads((source_root/'download_status.json').read_text())
    if status['completed_files']!=84 or status['failed_files'] or not status['download_finished']:
        raise RuntimeError('The complete frozen development cohort is required')
    files={r['path']:r for r in status['files']}
    if set(files)!={r['path'] for r in plan['files']}:raise RuntimeError('Source selection changed')
    for file in files.values():
        if sha(file['path'])!=file['sha256']:raise RuntimeError('Downloaded source changed')
    sys.path.insert(0,str(EXP/'visual_scope_relations_v8'))
    from longitudinal_probe import audit_report
    dest=OUT/'expanded_development_sources';rows=[]
    originals=[r for r in plan['files'] if r['kind']=='native_report']
    for index,record in enumerate(originals,1):
        key=record['lei']+'_'+record['period_end'];target=dest/'scoring_only'/(key+'.json')
        html=Path(record['path']);facts=html.with_name('published_facts_scoring_only.json')
        if target.exists():
            audit=json.loads(target.read_text())
            if audit['xhtml_sha256']!=sha(html) or audit['facts_sha256']!=sha(facts):
                raise RuntimeError('Existing source quality audit differs')
        else:
            audit=audit_report(html,facts);save(target,audit)
        rows.append({'lei':record['lei'],'name':record['name'],'period_end':record['period_end'],
            'filing_id':record['filing_id'],'source_xhtml_sha256':sha(html),'publisher_facts_sha256':sha(facts),
            'counts':audit['counts'],'formats':audit['formats'],
            'partial_publisher_teacher_only':True,'visual_visibility_and_header_ownership_certified':False})
        save(dest/'progress.json',{'completed_reports':len(rows),'expected_reports':len(originals)})
        print('Development publisher audit',index,'/',len(originals),record['name'],record['period_end'],flush=True)
    totals=Counter()
    for row in rows:totals.update(row['counts'])
    save(dest/'source_audit_manifest.json',{'reports':rows,'aggregate_counts':dict(totals),
        'legal_entity_identifiers':len({r['lei'] for r in rows}),'independent_company_groups_certified':False,
        'source_and_template_overlap_audit_complete':False,
        'native_visibility_and_scope_owner_certified':False,
        'calibration_or_held_out_sources_accessed':False,'human_confirmations':0,
        'download_plan_sha256':sha(source_root/'download_plan.json'),
        'download_status_sha256':sha(source_root/'download_status.json'),
        'auditor_source_sha256':sha(EXP/'visual_scope_relations_v8/longitudinal_probe.py'),
        'runner_sha256':sha(__file__)})
    print('Complete publisher source audit:',len(rows),'reports; native geometry/ownership still pending',flush=True)


if __name__=='__main__':main()
