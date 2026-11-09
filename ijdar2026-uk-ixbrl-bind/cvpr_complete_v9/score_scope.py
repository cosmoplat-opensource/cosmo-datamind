"""Post-freeze partial quantity/region/year diagnostic; not full binding accuracy."""
from collections import defaultdict
import argparse
from decimal import Decimal
import json
import re
from pathlib import Path
import statistics

from prepare import EXP, OUT, save, sha

OLD = EXP / 'runs/visual_scope_relations_v8_20261009/additional_native_development_v1'
POLICIES = ('hash_prefix', 'public_information_prefix')
METHODS = ('factor_memory', 'generic_joint', 'shared_neighborhood', 'explicit_scope')
SEEDS = (19, 41, 73)


def year_compatible(question, program):
    # Identical public-year criterion to the earlier diagnostic; no hidden context.
    dates = re.findall(r'\b\d{4}-\d{2}-\d{2}\b', question)
    return bool(dates and program and
        program.get('period_support', {}).get('year') == dates[-1][:4])


def correct_quantity(program, truth):
    unit = lambda v: str(v).replace('iso4217:', '').replace('xbrli:', '')
    return bool(program and truth['answer_scorable'] and
        Decimal(program['computed_answer']) in {Decimal(v) for v in truth['canonical_values']} and
        unit(program['unit']) == unit(truth['canonical_unit']))


def score_row(record, item, truth, cells):
    p = record['prediction']; top = p['forced_top1_program']; selected = p['executed_programs']
    answered = p['decision'] == 'answer'
    quantity = correct_quantity(top, truth)
    region = bool(top and top['cell'] in cells)
    year = year_compatible(item['query']['question'], top)
    selective = bool(answered and selected and all(correct_quantity(v, truth) for v in selected))
    return {**{k: record[k] for k in ('task_id', 'report', 'company', 'method', 'budget', 'seed')},
        'scorable': truth['answer_scorable'], 'forced_top1_is_null': top is None,
        'forced_top1_quantity_unit_correct': quantity, 'forced_top1_region_consistent': region,
        'forced_top1_quantity_region_year_correct': quantity and region and year,
        'declared_answer': answered, 'declared_quantity_unit_correct': selective,
        'declared_quantity_region_year_correct': bool(selective and all(v['cell'] in cells and
            year_compatible(item['query']['question'], v) for v in selected)),
        'declared_wrong': answered and truth['answer_scorable'] and not selective,
        'ambiguous': p['decision'] == 'ambiguous', 'no_candidate': p['decision'] == 'no_candidate',
        'credible_set_has_correct_quantity': any(correct_quantity(v, truth) for v in selected),
        'pool_has_correct_quantity': any(correct_quantity(v, truth) for v in item['candidates']),
        'pool_has_publisher_region': any(v['cell'] in cells for v in item['candidates']),
        'pool_has_quantity_region_and_query_year': any(correct_quantity(v, truth) and v['cell'] in cells and
            year_compatible(item['query']['question'], v) for v in item['candidates'])}


def counts(rows):
    fields = [k for k in rows[0] if k not in ('task_id', 'report', 'company', 'method', 'budget', 'seed')]
    return {'tasks': len(rows), **{k: sum(bool(r[k]) for r in rows) for k in fields}}


def verify(policy, training_run='training'):
    folder = OUT / 'scope_pilot' / policy
    dest = folder / training_run; meta = json.loads((dest / 'inference_manifest.json').read_text())
    features = json.loads((folder / 'features/manifest.json').read_text())
    source = Path(features['source_public_input'])
    for path, digest in ((dest / 'predictions.json', meta['predictions_sha256']),
                         (dest / 'design.json', meta['design_sha256']),
                         (source, meta['public_inputs_sha256'])):
        if sha(path) != digest:
            raise RuntimeError('Frozen scope artifacts changed: ' + str(path))
    design = json.loads((dest / 'design.json').read_text())
    if sha(folder / 'features/manifest.json') != design['features_manifest_sha256']:
        raise RuntimeError('Scope feature manifest changed')
    for name in ('packs', 'reports'):
        if sha(folder / 'features' / (name + '.json')) != features[name + '_sha256']:
            raise RuntimeError('Scope features changed')
    reports = json.loads((folder / 'features/reports.json').read_text())
    for report in reports.values():
        if sha(folder / report['features']) != report['features_sha256']:
            raise RuntimeError('Frozen node features changed')
    data = json.loads(source.read_text())
    rows = json.loads((dest / 'predictions.json').read_text())
    expected = {('shared_zero_feedback', 0, None, q) for q in data['evaluation_ids']}
    expected |= {(m, b, s, q) for m in METHODS for b in (5, 10) for s in SEEDS for q in data['evaluation_ids']}
    actual = {(r['method'], r['budget'], r['seed'], r['task_id']) for r in rows}
    if actual != expected or len(actual) != len(rows) or len(rows) != meta['expected_predictions']:
        raise RuntimeError('Incomplete or duplicate scope matrix')
    ledger = json.loads((dest / 'training_ledger.json').read_text())
    expected_models = {(c, b, m, s) for c in {r['company'] for r in rows}
        for b in (5, 10) for m in METHODS for s in SEEDS}
    actual_models = {(r['company'], r['budget'], r['method'], r['seed']) for r in ledger}
    if actual_models != expected_models or len(ledger) != len(expected_models):
        raise RuntimeError('Incomplete trained model ledger')
    budgets = defaultdict(set)
    for r in ledger:
        path = dest / 'checkpoints' / f"{r['company']}_{r['budget']}_{r['method']}_{r['seed']}.pt"
        if sha(path) != r['checkpoint_sha256'] or r['design_sha256'] != meta['design_sha256']:
            raise RuntimeError('Trained checkpoint changed')
        budgets[r['company'], r['budget']].add((r['feedback_slots'], r['usable_answer_bags']))
    if any(len(v) != 1 for v in budgets.values()):
        raise RuntimeError('Methods received different supervision budgets')
    return data, rows, ledger


def main(training_run='training'):
    verified = {policy: verify(policy, training_run) for policy in POLICIES}
    zeros = [[r for r in verified[p][1] if r['budget'] == 0] for p in POLICIES]
    if zeros[0] != zeros[1]:
        raise RuntimeError('Zero-feedback public predictions must be identical')
    # Both complete policies and their trained checkpoints were verified before gold access.
    gold_path = OLD / 'quantity_frontend_v1/scoring_only.json'
    mapping_path = OLD / 'quantity_frontend_v1/native_gold_mapping_scoring_only.json'
    gold = json.loads(gold_path.read_text()); regions = defaultdict(set)
    for row in json.loads(mapping_path.read_text()):
        regions[row['report'], row['published_region']].update(row['native_region_candidates'])
    results = []
    for policy, (data, predictions, ledger) in verified.items():
        scored = []
        for r in predictions:
            truth = gold[r['task_id']]
            cells = set().union(*(regions[r['report'], g] for g in truth['published_region_ids']))
            scored.append(score_row(r, data['inputs'][r['task_id']], truth, cells))
        groups = defaultdict(list)
        for r in scored:
            groups[r['method'], r['budget'], r['seed']].append(r)
        by_seed = [{'method': m, 'budget': b, 'seed': s, 'counts': counts(rows),
            'by_company': {c: counts([r for r in rows if r['company'] == c]) for c in sorted({r['company'] for r in rows})}}
            for (m, b, s), rows in groups.items()]
        aggregate = []
        for method in ('shared_zero_feedback',) + METHODS:
            for budget in ((0,) if method == 'shared_zero_feedback' else (5, 10)):
                runs = [r['counts'] for r in by_seed if r['method'] == method and r['budget'] == budget]
                aggregate.append({'method': method, 'budget': budget, 'per_seed_counts': runs,
                    'mean_counts_over_seeds': {k: statistics.mean(v[k] for v in runs) for k in runs[0]}})
        result = {'policy': policy, 'training_run': training_run, 'aggregate': aggregate, 'by_seed': by_seed,
            'models': len(ledger), 'predictions': len(predictions), 'independent_companies': 2,
            'training_feedback': sorted({(r['company'], r['budget'], r['feedback_slots'], r['usable_answer_bags']) for r in ledger}),
            'development_only': True, 'human_confirmations': 0,
            'full_binding_accuracy_certified': False, 'risk_calibrated': False,
            'scoring_after_all_predictions_frozen': True,
            'references_sha256': sha(gold_path), 'region_mapping_sha256': sha(mapping_path),
            'scorer_sha256': sha(__file__)}
        dest = OUT / 'scope_pilot' / policy / training_run
        save(dest / 'scoring_outcomes.json', scored); save(dest / 'scoring_results.json', result)
        (dest / 'scorer_snapshot.py').write_bytes(Path(__file__).read_bytes())
        results.append(result)
        print(policy)
        for row in aggregate:
            c = row['mean_counts_over_seeds']
            print(row['method'], row['budget'], 'quantity', round(c['forced_top1_quantity_unit_correct'], 2),
                'region/year', round(c['forced_top1_quantity_region_year_correct'], 2),
                'answered', round(c['declared_answer'], 2), 'wrong', round(c['declared_wrong'], 2))
    save(OUT / 'scope_pilot' / (training_run + '_comparison_results.json'), results)


if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--training-run',default='training',
        choices=('training','training_anchored_null_v2','training_balanced_null_v3'));main(parser.parse_args().training_run)
