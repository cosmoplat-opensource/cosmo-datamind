"""Development-only reference-query repair and strict public/scoring separation.

SQL agreement does not validate visible signs, scales, units or identity.
The supplied red rectangle is an annotation-assisted track for all methods.
No held-out assets are opened or original labels replaced.
"""
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import sqlite3

EXP=Path(__file__).resolve().parents[1]
SOURCE=EXP/'visual_bench_v0'
OUT=EXP/'runs/cvpr_complete_v9_20261013'
PUBLIC_FIELDS={'task_id','source_group','document_group','question','image','track'}


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def opaque(value):return hashlib.sha256(str(value).encode()).hexdigest()[:20]
def save(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n');tmp.replace(path)


def repair_sql(question):
    sql=question['sql'];kind=question['template'].split(':')[-1]
    if kind=='b_yoy_change_percent':
        sql=sql.replace('SELECT p.fact_id FROM fact_dims GROUP BY p.fact_id','SELECT fact_id FROM fact_dims GROUP BY fact_id')
        sql=sql.replace('SELECT p.fact_id FROM fact_dims','SELECT fact_id FROM fact_dims')
    if kind=='c_largest_calc_component' and 'f.fact_id NOT IN (SELECT fact_id FROM fact_dims)' not in sql:
        sql=sql.replace('WHERE k.parent_code=','WHERE f.fact_id NOT IN (SELECT fact_id FROM fact_dims) AND k.parent_code=')
    return sql


def expected(question):
    answer=question['answer'];kind=question['template'].split(':')[-1]
    if isinstance(answer,dict):return [answer['label'],answer['value']]
    return [answer,question['answer_unit']] if kind=='d_base_units' else [answer]


def equal(a,b):
    if isinstance(a,(int,float)) and isinstance(b,(int,float)):
        return math.isfinite(a) and math.isfinite(b) and math.isclose(a,b,rel_tol=1e-9,abs_tol=1e-6)
    return a==b


def public_record(question):
    ident=opaque(question['qid'])
    return {'task_id':ident,'source_group':opaque(question['lei']),
            'document_group':opaque(question['report_id']),'question':question['question'],
            'image':'images/'+ident+'.jpg','track':'supplied_target_rectangle_no_hidden_labels'}


def prepare(source=SOURCE,out=OUT):
    source=Path(source);out=Path(out);file=source/'questions/dev/questions.json'
    questions=json.loads(file.read_text());ids=[q['qid'] for q in questions]
    if len(ids)!=len(set(ids)):raise ValueError('Duplicate task IDs')
    public=[];scoring=[];repairs=[];pages={};counts=Counter();connections={};input_hashes={str(file):sha(file)}
    try:
        for q in questions:
            image=(source/q['highlighted_image']).resolve();db=(source/q['db']).resolve()
            if not image.is_file():raise ValueError('Source image missing')
            if db not in connections:
                connections[db]=sqlite3.connect(db.as_uri()+'?mode=ro',uri=True);input_hashes[str(db)]=sha(db)
            sql=repair_sql(q);rows=connections[db].execute(sql).fetchall();want=expected(q)
            if len(rows)!=1 or len(rows[0])!=len(want) or not all(equal(a,b) for a,b in zip(rows[0],want)):
                raise ValueError('Reference SQL still disagrees for '+q['qid'])
            p=public_record(q);public.append(p)
            target=out/'public'/p['image'];target.parent.mkdir(parents=True,exist_ok=True)
            if target.exists() and sha(target)!=sha(image):raise ValueError('Frozen image changed')
            if not target.exists():target.symlink_to(image)
            original=(source/q['image']).resolve();page_id=opaque(sha(original))
            page_target=out/'public/images'/('page-'+page_id+'.jpg')
            if not page_target.exists():page_target.symlink_to(original)
            pages[page_id]={'page_id':page_id,'source_group':p['source_group'],'document_group':p['document_group'],
                           'image':'images/'+page_target.name,'image_sha256':sha(original)}
            scoring.append({'task_id':p['task_id'],'source_qid':q['qid'],'reference':q['answer'],
                 'answer_unit':q['answer_unit'],'reference_sql':sql,'db':str(db),
                 'template':q['template'],'source_labels':q['gold'],
                 'visual_semantics_status':'requires_independent_visible_evidence_audit'})
            counts[q['template']]+=1
            if sql!=q['sql']:repairs.append({'task_id':p['task_id'],'source_qid':q['qid'],'original_sql':q['sql'],'repaired_sql':sql})
            input_hashes[str(image)]=sha(image)
    finally:
        for con in connections.values():con.close()
    if sha(file)!=input_hashes[str(file)]:raise ValueError('Original questions changed')
    if any(set(p)!=PUBLIC_FIELDS for p in public):raise ValueError('Public whitelist violated')
    save(out/'public/tasks.json',public);save(out/'scoring_only/references.json',scoring)
    save(out/'public/pages.json',list(pages.values()))
    save(out/'scoring_only/reference_repairs.json',repairs)
    save(out/'input_hashes.json',input_hashes)
    protocol={'stage':'E0 development data preparation','tasks':len(public),
      'companies':len({p['source_group'] for p in public}),'reports':len({p['document_group'] for p in public}),
      'public_pages':len(pages),
      'reference_sql_agreement':len(scoring),'reference_sql_repairs':len(repairs),
      'public_fields':sorted(PUBLIC_FIELDS),'templates':dict(counts),
      'original_sources_modified':False,'held_out_assets_opened':False,'human_confirmations':0,
      'target_geometry':'provided red rectangle; an oracle localization input',
      'visual_labels_certified':False,'method_evaluation_completed':False,
      'code_sha256':sha(__file__)}
    save(out/'preparation.json',protocol);print(json.dumps(protocol,ensure_ascii=False))
    return protocol


if __name__=='__main__':prepare()
