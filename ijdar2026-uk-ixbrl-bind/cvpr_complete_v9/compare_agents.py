"""Paired statistics for complete, post-freeze development agent runs."""
from collections import Counter, defaultdict
import itertools
import json
from pathlib import Path

import numpy as np
from scipy.stats import binomtest
from prepare import OUT, save, sha

NAMES=('tat_dev_native_baseline','tat_dev_program_agent','tat_dev_memory_agent')


def main():
    scored={};inputs={};diagnostics={}
    for name in NAMES:
        folder=OUT/name;manifest=json.loads((folder/'inference_manifest.json').read_text())
        if not manifest['complete'] or sha(folder/'outcomes.json')!=manifest['outcomes_sha256']:
            raise RuntimeError('Only complete frozen agent runs can be compared')
        score=json.loads((folder/'scoring_results.json').read_text())
        if score['predictions_sha256']!=manifest['outcomes_sha256']:
            raise RuntimeError('Scores do not refer to the frozen inference outputs')
        rows=json.loads((folder/'scoring_outcomes.json').read_text())
        scored[name]={r['task_id']:r for r in rows}
        if len(rows)!=1644 or len(scored[name])!=1644:raise RuntimeError('Incomplete/duplicate development question set')
        inputs[name]={'predictions_sha256':sha(folder/'outcomes.json'),
            'scores_sha256':sha(folder/'scoring_outcomes.json'),'design_sha256':sha(folder/'design.json')}
        usage=Counter();failures=Counter();seconds=[]
        for path in (folder/'raw').glob('*.json'):
            raw=json.loads(path.read_text());seconds.append(raw.get('seconds',0))
            for k,v in raw.get('usage',{}).items():
                if type(v) in (int,float):usage[k]+=v
        for row in json.loads((folder/'outcomes.json').read_text()):
            if row['status']=='completed':continue
            if 'final_execution_feedback' in row:
                failures[row['final_execution_feedback'].get('execution_error',row['status'])]+=1
            elif 'turns' in row:
                failures[row['turns'][-1]['execution_feedback'].get('execution_error',row['status'])]+=1
            else:failures[row['status']]+=1
        diagnostics[name]={'physical_saved_API_responses':len(seconds),'physical_API_seconds_sum':sum(seconds),
            'physical_response_token_usage':dict(usage),'terminal_failure_categories':dict(failures),
            'common_first_call_reused':name=='tat_dev_memory_agent',
            'shared_first_call_tokens_not_counted_twice_in_physical_usage':True}
    qids=sorted(scored[NAMES[0]])
    if any(set(scored[n])!=set(qids) for n in NAMES):raise RuntimeError('Paired question identifiers differ')
    groups=defaultdict(list)
    for i,qid in enumerate(qids):groups[scored[NAMES[0]][qid]['document_id']].append(i)
    doc_ids=sorted(groups);indices=[np.array(groups[d]) for d in doc_ids];rng=np.random.default_rng(20261013)
    resamples=rng.integers(0,len(indices),size=(5000,len(indices)))
    pairs=[]
    for left,right in itertools.combinations(NAMES,2):
        a=np.array([scored[left][q]['em'] for q in qids]);b=np.array([scored[right][q]['em'] for q in qids])
        gained=int(((a==0)&(b==1)).sum());lost=int(((a==1)&(b==0)).sum())
        emdiff=b-a;f1diff=np.array([scored[right][q]['f1']-scored[left][q]['f1'] for q in qids])
        doc_n=np.array([len(ix) for ix in indices]);den=doc_n[resamples].sum(1)
        intervals={}
        for metric,diff in (('em',emdiff),('f1',f1diff)):
            sums=np.array([diff[ix].sum() for ix in indices]);boot=sums[resamples].sum(1)/den
            intervals[metric]={'right_minus_left':float(diff.mean()),'document_cluster_bootstrap_95ci':np.quantile(boot,[.025,.975]).tolist()}
        pairs.append({'left':left,'right':right,'right_only_correct':gained,'left_only_correct':lost,
            'question_level_exact_mcnemar_p':float(binomtest(gained,gained+lost).pvalue) if gained+lost else 1.,
            **intervals,'native_baseline_has_different_OCR_and_call_budget':left=='tat_dev_native_baseline'})
    result={'questions':len(qids),'official_documents':len(indices),'repeats_per_agent':1,
        'paired_comparisons':pairs,'diagnostics':diagnostics,'inputs':inputs,'bootstrap_samples':5000,
        'bootstrap_seed':20261013,'development_only':True,'human_confirmations':0,
        'document_clusters_not_certified_independent_company_clusters':True,
        'not_an_ontology_unique_advantage_test':True,'not_a_public_test_or_SOTA_result':True,
        'runner_sha256':sha(__file__)}
    save(OUT/'tat_development_paired_statistics.json',result)
    (OUT/'tat_development_paired_scorer_snapshot.py').write_bytes(Path(__file__).read_bytes())
    for pair in pairs:print(pair['left'],pair['right'],'EM difference',round(pair['em']['right_minus_left']*100,2),
        'EM document CI', [round(x*100,2) for x in pair['em']['document_cluster_bootstrap_95ci']],
        'McNemar p',round(pair['question_level_exact_mcnemar_p'],5),flush=True)


if __name__=='__main__':main()
