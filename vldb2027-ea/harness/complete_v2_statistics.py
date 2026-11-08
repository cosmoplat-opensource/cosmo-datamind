"""Offline database-cluster intervals, task-type breakdowns and recovery disclosure.

Repeats are averaged within question before database resampling. These post-hoc
intervals condition on frozen stores; two industrial databases cannot establish
generalization to an industrial population. No model calls or answer replacement.
"""
from collections import Counter, defaultdict
from pathlib import Path
import hashlib
import json
import numpy as np
import aggregate_v2 as ag


def paired_statistics(left, right, metadata, samples=10000):
    if set(left)!=set(right) or set(left)!={1,2,3}:
        raise ValueError('Exactly three paired repeats required')
    ids=set(left[1][0])
    if any(set(reps[r][0])!=ids for reps in (left,right) for r in (1,2,3)):
        raise ValueError('Paired question sets differ')
    if not ids.issubset(metadata): raise ValueError('Question metadata missing')
    if any(type(v) not in (int,bool) or v not in (0,1) for reps in (left,right) for r in reps for v in reps[r][0].values()):
        raise ValueError('Nonbinary score')
    groups=defaultdict(list)
    for qid in sorted(ids):groups[metadata[qid]['db_id']].append(qid)
    totals=[];by_db=[];by_kind=[]
    def row(qids,label):
        a=sum(sum(left[r][0][q] for r in (1,2,3))/3 for q in qids)
        b=sum(sum(right[r][0][q] for r in (1,2,3))/3 for q in qids)
        return {'group':label,'questions':len(qids),'left_percent':100*a/len(qids),
                'right_percent':100*b/len(qids),'difference_pp':100*(a-b)/len(qids)}
    for db,qids in sorted(groups.items()):
        d=row(qids,db);by_db.append(d);totals.append([len(qids),d['left_percent']*len(qids)/100,d['right_percent']*len(qids)/100])
    rng=np.random.default_rng(20261013);a=np.asarray(totals)
    chosen=a[rng.integers(0,len(a),(samples,len(a)))].sum(axis=1)
    intervals={name:np.quantile(values,[.025,.975]).tolist() for name,values in {
        'left_percent':100*chosen[:,1]/chosen[:,0], 'right_percent':100*chosen[:,2]/chosen[:,0],
        'difference_pp':100*(chosen[:,1]-chosen[:,2])/chosen[:,0]}.items()}
    kinds=sorted({metadata[q]['difficulty'] for q in ids})
    for kind in kinds:by_kind.append(row([q for q in sorted(ids) if metadata[q]['difficulty']==kind],kind))
    return {**row(sorted(ids),'all'),'databases':len(groups),'ci95':intervals,'by_database':by_db,
            'by_difficulty_or_task_type':by_kind,'repeat_tests':[{'repeat':r,'delta_correct':sum(left[r][0].values())-sum(right[r][0].values()),
                   'mcnemar_exact_p':ag.mcnemar(left[r][0],right[r][0])[0]} for r in (1,2,3)],
            'interval_interpretation':'post-hoc conditional database bootstrap; not independent repeats',
            'few_clusters':len(groups)<10}


def metadata_for(directions):
    out={}
    for direction in directions:
        for file in sorted((ag.DATA/ag.SPLITROOT[direction]/'test').glob('*.json')):
            for q in json.loads(file.read_text())['questions']:
                key=str(q['question_id'])
                if key in out:raise ValueError('Overlapping fold question ID')
                out[key]={'db_id':q['db_id'],'difficulty':q.get('difficulty','unspecified')}
    return out


def recovery_records():
    rows=[]
    for file in sorted((ag.V2/'qa').glob('*/*/repair_log.json')):
        for record in json.loads(file.read_text()):
            rows.append({'direction':file.parent.parent.name,'unit':file.parent.name,
                         'log':str(file.relative_to(ag.EXP)),**record})
    return rows


def frozen_input_manifest():
    """Hash formal prediction candidates, split metadata and the experiment plan.

    Backups are deliberately excluded, consistently with the score reader.
    Hash streaming keeps large saved agent traces out of the statistics RAM.
    """
    paths={ag.V2/'plan.json'}
    for job in json.loads((ag.V2/'plan.json').read_text()):
        if job['phase']!='qa':continue
        root=Path(job.get('reuse_of') or job['out'])
        _,direction,ontology,consumer,tail=job['id'].split('__')
        if consumer=='react':
            files=sorted(root.glob('*/*/all_results.json'))
            if files:paths.add(files[-1])
        else:
            paths.update(f for f in root.glob('*.json') if f.name not in ('meta.json','repair_log.json')
               and not f.stem.count('.') and not any(s in f.stem for s in ('before_repair','backup','aborted')))
    for split in ag.SPLITROOT.values():paths.update((ag.DATA/split/'test').glob('*.json'))
    rows={}
    for path in sorted(paths):
        digest=hashlib.sha256()
        with path.open('rb') as file:
            for block in iter(lambda:file.read(1024*1024),b''):digest.update(block)
        try:name=str(path.relative_to(ag.EXP))
        except ValueError:name=str(path)
        rows[name]={'sha256':digest.hexdigest(),'bytes':path.stat().st_size}
    return rows


def main():
    dest=ag.V2/'completion_20261013';dest.mkdir(exist_ok=True)
    units=ag.collect();results={};lines=['# 协议 v2：最终统计与恢复披露','',
      '三次重复先在同一道题内平均，再按数据库整组配对重采样 10,000 次。区间为事后、固定本体条件下的探索性区间。',
      '工业库仅有两个合成来源，区间不能作为跨企业泛化的可靠证据；工业 difficulty 字段是任务类型，不是难度等级。','',
      '| 比较 | 题数 / 库数 | 左 / 右（%） | 差值（百分点） | 按库95%区间 |',
      '|---|---:|---:|---:|---|']
    for pool,dirs in ag.POOLS.items():
        meta=metadata_for(dirs)
        for setting in ('full','nofk','bare'):
            contrasts=[('native',('dm-w','dm'),('evo-w','react')),
                       ('construction_d',('dm-d','dm'),('evo-d','dm')),
                       ('construction_w',('dm-w','dm'),('evo-w','dm')),
                       ('ontology_d',('dm-d','dm'),('none','dm')),
                       ('ontology_w',('dm-w','dm'),('none','dm'))]
            contrasts += [('consumer_'+o,(o,'dm'),(o,'react')) for o in ('none','evo-d','evo-w','dm-d','dm-w')]
            for label,a,b in contrasts:
                left=ag.pooled(units,dirs,*a,setting);right=ag.pooled(units,dirs,*b,setting)
                # Only explicitly collected cells are used; do not silently create a no-FK baseline.
                if not left or not right:continue
                key=f'{pool}|{setting}|{label}';r=paired_statistics(left,right,meta);r.update(left_condition=a,right_condition=b)
                results[key]=r;lo,hi=r['ci95']['difference_pp']
                lines.append(f"| {key.replace('|',' / ')} | {r['questions']} / {r['databases']} | {r['left_percent']:.2f} / {r['right_percent']:.2f} | {r['difference_pp']:+.2f} | [{lo:+.2f}, {hi:+.2f}] |")
    for key,r in results.items():
        if not key.endswith('|native'):continue
        lines += ['',f'## {key}：按库及难度/任务类型','', '| 分组 | 题数 | 左（%） | 右（%） | 差（百分点） |','|---|---:|---:|---:|---:|']
        for part in ('by_database','by_difficulty_or_task_type'):
            for row in r[part]:lines.append(f"| {row['group']} | {row['questions']} | {row['left_percent']:.2f} | {row['right_percent']:.2f} | {row['difference_pp']:+.2f} |")
    recoveries=recovery_records();unique={(r['direction'],r['unit'],db,str(q)) for r in recoveries for db,ids in r.get('ids',{}).items() for q in ids}
    lines += ['','## 附录：恢复过程','',f'当前累计 {len({(r["direction"],r["unit"]) for r in recoveries})} 个补跑单元，{len(unique)} 个单元内题目；实际补跑记录 {sum(r.get("n_rerun",0) for r in recoveries)} 条（含同一接口失败题的重复恢复尝试）。',
              '原报告的 890 题为历史汇总；下列逐次日志保留全部恢复尝试，不把尝试数当作独立题数。',
              '2026-10-13 最后一题补跑仍因 1,672,755 token 的语义提示超过 1,048,576 token 上限失败。按原配置保留零分，不单独缩短提示、重建本体或选择更有利答案。',
              '10-10 断网影响的整单元重跑仍按 outage_20261010.md 披露。完整逐题恢复清单在本目录 recovery_records.json。','',
              '原逐题 McNemar 检验与新的按库区间分别报告；本次没有预登记的多重比较控制，不将若干显著性检验的累积当作独立确认。']
    (dest/'statistics.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
    (dest/'recovery_records.json').write_text(json.dumps(recoveries,ensure_ascii=False,indent=2)+'\n')
    (dest/'最终统计与恢复披露.md').write_text('\n'.join(lines)+'\n')
    manifest={str(f.relative_to(ag.EXP)):hashlib.sha256(f.read_bytes()).hexdigest() for f in (ag.V2/'qa').glob('*/*/repair_log.json')}
    (dest/'recovery_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (dest/'statistics_input_manifest.json').write_text(json.dumps(frozen_input_manifest(),indent=2)+'\n')
    print(json.dumps({'contrasts':len(results),'recovery_units':len({(r['direction'],r['unit']) for r in recoveries}),'unique_unit_question_pairs':len(unique)}))


if __name__=='__main__':main()
