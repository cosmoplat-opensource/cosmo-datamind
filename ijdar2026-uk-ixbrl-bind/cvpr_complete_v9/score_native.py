"""Post-inference TAT-DQA scoring with pinned, unaltered author metric files."""
from collections import Counter, defaultdict
import argparse
import json
from pathlib import Path
import sys

from prepare import EXP, OUT, save, sha
from public_benchmarks import ROOT


def verify_predictions(tasks, rows, manifest):
    ids = [t['task_id'] for t in tasks]
    predicted = [r['task_id'] for r in rows]
    if len(ids) != len(set(ids)) or len(predicted) != len(set(predicted)):
        raise RuntimeError('Duplicate question identifiers')
    if set(ids) != set(predicted) or len(rows) != manifest['expected_tasks'] or not manifest['complete']:
        raise RuntimeError('A complete frozen inference run is required')


def load_metric():
    root = EXP / 'ext/doc2soar_metric_snapshot'
    provenance = json.loads((root / 'provenance.json').read_text())
    for name, digest in provenance['sha256'].items():
        if sha(root / name) != digest:
            raise RuntimeError('Pinned author scoring source changed: ' + name)
    sys.path.insert(0, str(root))
    from tatqa_metric import extract_gold_answers, get_answer_str, get_metrics, metric_max_over_ground_truths

    def score(annotation, answer):
        if answer is None:
            return {'em': 0., 'f1': 0., 'scale_matches': False}
        _, gold, scale = extract_gold_answers(annotation)
        predicted = answer['answer'] if isinstance(answer['answer'], list) else [answer['answer']]
        em, f1 = metric_max_over_ground_truths(get_metrics,
            get_answer_str(predicted, answer['scale']), get_answer_str(gold, scale))
        return {'em': float(em), 'f1': float(f1), 'scale_matches': answer['scale'] == scale}
    return score, provenance


def summarize(rows):
    if not rows:
        return {'questions': 0, 'em': None, 'f1': None, 'scale_accuracy': None}
    return {'questions': len(rows), 'em': sum(r['em'] for r in rows) / len(rows),
        'f1': sum(r['f1'] for r in rows) / len(rows),
        'scale_accuracy': sum(r['scale_matches'] for r in rows) / len(rows)}


def main(run_name='tat_dev_native_baseline'):
    root = ROOT / 'tatdqa/dev'
    folder = OUT / run_name
    manifest = json.loads((folder / 'inference_manifest.json').read_text())
    design = json.loads((folder / 'design.json').read_text())
    for path, digest in ((folder / 'design.json', manifest['design_sha256']),
                         (folder / 'outcomes.json', manifest['outcomes_sha256']),
                         (root / 'public/tasks.json', design['public_tasks_sha256']),
                         (root / 'common_perception/manifest.json', design.get('native_image_manifest_sha256') or design['common_perception_sha256'])):
        if sha(path) != digest:
            raise RuntimeError('Frozen inference input/output changed: ' + str(path))
    tasks = json.loads((root / 'public/tasks.json').read_text())
    expected = design.get('expected_tasks', design.get('expected_questions'))
    if expected < len(tasks):
        if run_name not in ('tat_dev_program_smoke', 'tat_dev_native_smoke', 'tat_dev_memory_smoke'):
            raise RuntimeError('Subset scoring is only allowed for declared implementation checks')
        tasks = sorted(tasks, key=lambda q: q['task_id'])[:expected]
    rows = json.loads((folder / 'outcomes.json').read_text())
    verify_predictions(tasks, rows, manifest)
    # Scoring annotations are opened only after the whole inference run is verified.
    reference_path = root / 'scoring_only/references.json'
    reference_rows = json.loads(reference_path.read_text())
    references = {r['task_id']: r['reference'] for r in reference_rows}
    if len(references) != len(reference_rows) or not {t['task_id'] for t in tasks} <= set(references):
        raise RuntimeError('Reference question identifiers do not match public questions')
    task_map = {t['task_id']: t for t in tasks}
    score, provenance = load_metric()
    scored = []
    for row in rows:
        qid = row['task_id']; reference = references[qid]
        answer = row.get('answer') if row['status'] == 'completed' else None
        scored.append({'task_id': qid, 'document_id': task_map[qid]['document_id'],
            'source_group': task_map[qid]['source_group'], 'answer_type': reference['answer_type'],
            'status': row['status'], **score(reference, answer)})
    by_type = defaultdict(list); by_document = defaultdict(list)
    for r in scored:
        by_type[r['answer_type']].append(r); by_document[r['document_id']].append(r)
    result = {'dataset': 'TAT-DQA', 'split': 'official development',
        'method': design.get('method', 'direct native-image DeepSeek V4.1'), 'model': design['model'],
        'overall': summarize(scored), 'status_counts': dict(Counter(r['status'] for r in rows)),
        'by_answer_type': {k: summarize(v) for k, v in sorted(by_type.items())},
        'by_document': {k: summarize(v) for k, v in sorted(by_document.items())},
        'invalid_outputs_scored_zero_and_retained_in_denominator': True,
        'reference_files_used_only_after_inference': True, 'test_inference_completed': False,
        'implementation_check_subset': expected < 1644,
        'metric_provenance': provenance, 'design_sha256': sha(folder / 'design.json'),
        'predictions_sha256': sha(folder / 'outcomes.json'), 'references_sha256': sha(reference_path),
        'scorer_sha256': sha(__file__)}
    save(folder / 'scoring_outcomes.json', scored)
    save(folder / 'scoring_results.json', result)
    (folder / 'scorer_snapshot.py').write_bytes(Path(__file__).read_bytes())
    print(json.dumps({k: result[k] for k in ('overall', 'status_counts', 'by_answer_type')}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--run', default='tat_dev_native_baseline',
        choices=('tat_dev_native_baseline', 'tat_dev_program_agent', 'tat_dev_memory_agent',
                 'tat_dev_native_smoke', 'tat_dev_program_smoke', 'tat_dev_memory_smoke'))
    main(parser.parse_args().run)
