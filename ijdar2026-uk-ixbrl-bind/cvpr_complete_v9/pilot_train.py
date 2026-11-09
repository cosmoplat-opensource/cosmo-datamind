"""Resumable equal-input, three-seed development scope comparison.

Only already allowed historical scalar/unit feedback supplies labels. Scope
ownership is latent; this pilot cannot certify full visual semantic bindings.
"""
import argparse
from decimal import Decimal
import json
from pathlib import Path
import time

import numpy as np
import torch

from prepare import OUT,save,sha
from pilot_features import POLICIES,OLD,D
from scope_model import ScopeModel,METHODS,latent_answer_loss

SEEDS=(19,41,73);STEPS=256;LR=.0003


def normalized_unit(unit):return str(unit).replace('iso4217:','').replace('xbrli:','')


def answer_bag_objective(scores, eligible, positive, anchor_null=False):
    if anchor_null:
        # Inference compares programs against a fixed zero-logit null option.
        # Include that same option in the training denominator; otherwise a
        # common logit shift is unidentifiable and changes abstention arbitrarily.
        scores=torch.cat((scores,scores.new_zeros(1)))
        eligible=torch.cat((eligible,torch.ones(1,dtype=torch.bool)))
        positive=torch.cat((positive,torch.zeros(1,dtype=torch.bool)))
    return latent_answer_loss(scores,eligible,positive)


def balanced_visual_block(nodes):
    if nodes.ndim!=2 or nodes.shape[1]!=D:raise ValueError('Unexpected common feature schema')
    result=nodes.clone()
    result[:,:768]=torch.nn.functional.normalize(result[:,:768],dim=1)
    return result


def answer_masks(item,pack,event):
    eligible=torch.tensor([s!='contradiction' for s in pack['period_status']],dtype=torch.bool)
    if event.get('answer_in_base_units') is None or event.get('answer_unit') is None:
        return eligible,torch.zeros_like(eligible)
    positives=torch.tensor([Decimal(p['computed_answer'])==Decimal(event['answer_in_base_units'])
       and normalized_unit(p['unit'])==normalized_unit(event['answer_unit']) for p in item['candidates']],dtype=torch.bool)
    return eligible,positives&eligible


def average_paths(scores,indices,count):
    maxima=scores.new_full((count,),-torch.inf)
    maxima.scatter_reduce_(0,indices,scores,reduce='amax',include_self=True)
    weights=(scores-maxima.detach()[indices]).exp()
    total=scores.new_zeros(count).index_add_(0,indices,weights)
    numbers=scores.new_zeros(count).index_add_(0,indices,torch.ones_like(scores))
    if (numbers==0).any():raise RuntimeError('Numeric program lost all evidence paths')
    return maxima.detach()+total.log()-numbers.log()


def decode(logits,item,pack):
    eligible=torch.tensor([s!='contradiction' for s in pack['period_status']]+[True])
    probabilities=logits.masked_fill(~eligible,-torch.inf).softmax(0)
    programs=item['candidates'];null=len(programs);order=probabilities.argsort(descending=True,stable=True).tolist()
    credible=[];mass=0.
    for j in order:
        credible.append(j);mass+=float(probabilities[j])
        if mass>=.95:break
    selected=[programs[j] for j in credible if j!=null]
    outcomes={(str(Decimal(p['computed_answer']).normalize()),normalized_unit(p['unit'])) for p in selected}
    unknown=any(pack['period_status'][j]=='unknown' for j in credible if j!=null)
    placeholder=any(p['quantity_status']=='placeholder_may_also_be_nonquantity' for p in selected)
    answer=(null not in credible and len(outcomes)==1 and not unknown and not placeholder
       and all('unknown' not in p['unit'] for p in selected))
    return {'decision':'answer' if answer else ('no_candidate' if order[0]==null else 'ambiguous'),
        'executed_programs':selected,'credible_program_ids':[p['id'] for p in selected],
        'null_in_credible_set':null in credible,'null_probability':float(probabilities[null]),
        'credible_mass':mass,'probabilities':probabilities.tolist(),
        'forced_top1_program':None if order[0]==null else programs[order[0]],
        'unknown_period_in_credible_set':unknown,'placeholder_ambiguity':placeholder,
        'posterior_not_calibrated':True,'support_scope_or_full_interval_not_certified':True}


def run(policy, anchor_null=False, balance_visual=False):
    torch.set_num_threads(1);torch.use_deterministic_algorithms(True)
    folder=OUT/'scope_pilot'/policy;fm=json.loads((folder/'features/manifest.json').read_text())
    source=Path(fm['source_public_input'])
    if sha(source)!=fm['source_public_input_sha256']:raise RuntimeError('Public inputs changed')
    for name in ('packs','reports'):
        if sha(folder/'features'/f'{name}.json')!=fm[name+'_sha256']:raise RuntimeError('Public features changed')
    data=json.loads(source.read_text());features=json.loads((folder/'features/packs.json').read_text())
    reports=json.loads((folder/'features/reports.json').read_text());rt={};packs={}
    for report,meta in reports.items():
        file=folder/meta['features']
        if sha(file)!=meta['features_sha256']:raise RuntimeError('Frozen node features changed')
        nc=len(meta['cells']);nh=len(meta['headers'])
        nodes=torch.tensor(np.load(file))
        if balance_visual:nodes=balanced_visual_block(nodes)
        rt[report]={'nodes':nodes, 'cell_indices':torch.arange(nc),
            'header_indices':torch.arange(nc,nc+nh),'header_roles':torch.tensor(meta['header_roles'])}
    for ident,pack in features.items():
        packs[ident]={'paths':torch.tensor(pack['paths'],dtype=torch.long),
            'program_indices':torch.tensor(pack['program_indices'],dtype=torch.long),
            'query':torch.tensor(pack['query'],dtype=torch.float32),'prior':torch.tensor(pack['prior']+[0.])}
    def make(method,seed):
        torch.manual_seed(seed);return ScopeModel(method,D,roles=2)
    params={m:sum(p.numel() for p in make(m,19).parameters()) for m in METHODS}
    design={'policy':policy,'methods':METHODS,'seeds':SEEDS,'steps_per_model':STEPS,'lr':LR,
      'optimizer':'AdamW','weight_decay':.01,'maximum_parameters':1000000,'actual_parameters':params,
      'common_hidden':128,'transformer_layers':2,'attention_heads':4,'roles':['period','unit'],
      'feedback_budgets':[0,5,10],'credible_mass':.95,'calibrated_same_risk_comparison':False,
      'same_numeric_pool_and_public_prior':True,'same_past_scalar_and_unit_feedback':True,
      'loss':'answer-compatible latent program marginal likelihood; no ownership BCE labels',
      'training_denominator_includes_inference_null_option':anchor_null,
      'visual_block_l2_normalized_for_all_controls':balance_visual,
      'full_concept_entity_period_unit_dimension_version_binding_certified':False,
      'company_clusters':2,'development_only':True,'human_confirmations':0,
      'features_manifest_sha256':sha(folder/'features/manifest.json'),
      'scope_model_sha256':sha(Path(__file__).with_name('scope_model.py')),'runner_sha256':sha(__file__)}
    suffix='training_balanced_null_v3' if balance_visual else ('training_anchored_null_v2' if anchor_null else 'training')
    dest=folder/suffix;dp=dest/'design.json'
    normalized=json.loads(json.dumps(design))
    if dp.exists() and json.loads(dp.read_text())!=normalized:raise RuntimeError('Frozen pilot design changed')
    save(dp,design)
    rows=[];ledger=[]
    for ident in data['evaluation_ids']:
        item=data['inputs'][ident]
        rows.append({'task_id':ident,'report':item['query']['report'],'company':item['query']['company'],
          'method':'shared_zero_feedback','budget':0,'seed':None,'prediction':decode(packs[ident]['prior'],item,features[ident])})
    def context(model,report):
        r=rt[report];enc=model.encode(r['nodes'])
        scope=model.scope_logits(enc,r['header_indices'],r['cell_indices'],r['header_roles'])
        return enc,scope
    def logits(model,ident,cache):
        report=features[ident]['report'];r=rt[report];p=packs[ident]
        if report not in cache:cache[report]=context(model,report)
        enc,scope=cache[report]
        scored=model.score_encoded(enc,r['header_indices'],r['cell_indices'],r['header_roles'],p['query'],p['paths'],scope_logits=scope)
        values=average_paths(scored['scores'],p['program_indices'],len(data['inputs'][ident]['candidates']))
        return p['prior']+torch.cat((values,values.new_zeros(1)))
    companies=sorted({item['query']['company'] for item in data['inputs'].values()})
    for company in companies:
        for budget in (5,10):
            events=[e for e in data['feedback_records'] if data['inputs'][e['id']]['query']['company']==company][:budget]
            permitted=json.loads((OLD/'shared_scope_heads_v1'/policy/'allowed_feedback'/f'allowed_answer_feedback_{company}_{budget}.json').read_text())
            if events!=permitted['feedback'] or permitted['future_report_answers_included'] is not False:raise RuntimeError('Feedback prefix changed')
            if any(data['inputs'][e['id']]['query']['source_year']!=2023 for e in events):raise RuntimeError('Future labels forbidden')
            active=[]
            for e in events:
                eligible,positive=answer_masks(data['inputs'][e['id']],features[e['id']],e)
                if positive.any():active.append((e['id'],eligible,positive))
            for method in METHODS:
                for seed in SEEDS:
                    model=make(method,seed);cp=dest/'checkpoints'/f'{company}_{budget}_{method}_{seed}.pt';mp=cp.with_suffix('.json')
                    if cp.exists() and mp.exists():
                        meta=json.loads(mp.read_text())
                        if sha(cp)!=meta['checkpoint_sha256'] or sha(dp)!=meta['design_sha256']:raise RuntimeError('Frozen checkpoint differs')
                        model.load_state_dict(torch.load(cp,weights_only=True))
                    else:
                        optimizer=torch.optim.AdamW(model.parameters(),lr=LR,weight_decay=.01);losses=[];started=time.monotonic()
                        for step in range(STEPS):
                            optimizer.zero_grad();cache={};loss=[]
                            for ident,eligible,positive in active:
                                s=logits(model,ident,cache)[:-1];loss.append(answer_bag_objective(s,eligible,positive,anchor_null))
                            value=sum(loss)/max(1,budget) if loss else sum(p.sum()*0 for p in model.parameters())
                            value.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),5);optimizer.step()
                            if step==0 or (step+1)%64==0:losses.append({'step':step+1,'answer_bag_nll':float(value.detach())})
                        cp.parent.mkdir(parents=True,exist_ok=True);torch.save(model.state_dict(),cp)
                        meta={'company':company,'budget':budget,'method':method,'seed':seed,'optimizer_steps':STEPS,
                           'feedback_slots':len(events),'usable_answer_bags':len(active),'parameters':params[method],
                           'wall_train_s':time.monotonic()-started,'losses':losses,
                           'checkpoint_sha256':sha(cp),'design_sha256':sha(dp)}
                        save(mp,meta)
                    model.eval();cache={}
                    with torch.no_grad():
                        for ident in data['evaluation_ids']:
                            item=data['inputs'][ident]
                            if item['query']['company']!=company:continue
                            rows.append({'task_id':ident,'report':item['query']['report'],'company':company,
                                'method':method,'budget':budget,'seed':seed,
                                'prediction':decode(logits(model,ident,cache),item,features[ident])})
                    ledger.append(meta);save(dest/'progress.json',{'models_completed':len(ledger),'expected_models':len(companies)*2*len(METHODS)*len(SEEDS)})
                    print('Trained development scope',policy,company,budget,method,seed,'seconds',round(meta['wall_train_s'],1),flush=True)
    expected=len(data['evaluation_ids'])*(1+len(METHODS)*2*len(SEEDS))
    if len(rows)!=expected:raise RuntimeError('Incomplete paired prediction matrix')
    save(dest/'predictions.json',rows);save(dest/'training_ledger.json',ledger)
    save(dest/'inference_manifest.json',{'expected_predictions':expected,'written_predictions':len(rows),
       'predictions_sha256':sha(dest/'predictions.json'),'design_sha256':sha(dp),
       'public_inputs_sha256':sha(source),'publisher_scoring_files_read':False,'scoring_completed':False})


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--policy',choices=POLICIES,required=True)
    p.add_argument('--anchor-inference-null',action='store_true');p.add_argument('--balance-visual-block',action='store_true')
    args=p.parse_args()
    if args.balance_visual_block and not args.anchor_inference_null:p.error('Visual scaling diagnostic uses the corrected null objective')
    run(args.policy,args.anchor_inference_null,args.balance_visual_block)
