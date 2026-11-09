"""Static semantic-example memory from the official TAT-DQA TRAIN split only.

This is training supervision, not human confirmation or test-time feedback.
Retrieval depends only on the current public question. Example numbers must
never be treated as evidence for a new report.
"""
import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import re

from prepare import OUT, save, sha
from public_benchmarks import ROOT

STOP = frozenset('a an and are as at be by did do does for from how in is it of on or that the their there these this to was were what when which with year years'.split())


def tokens(text):
    return [t for t in re.findall(r'[a-z]+', text.lower()) if t not in STOP]


def create_index(tasks, annotations):
    refs = {r['task_id']: r['reference'] for r in annotations}
    if len(refs) != len(annotations) or set(refs) != {t['task_id'] for t in tasks}:
        raise RuntimeError('Training question/reference identifiers differ')
    entries = []; postings = defaultdict(list); lengths = []
    for i, task in enumerate(sorted(tasks, key=lambda t: t['task_id'])):
        if task['split'] != 'train': raise RuntimeError('Only official training examples are allowed')
        ref = refs[task['task_id']]
        if task['question'] != ref['question']: raise RuntimeError('Training question changed')
        entries.append({'task_id': task['task_id'], 'question': task['question'],
            'answer': ref['answer'], 'scale': ref['scale'], 'answer_type': ref['answer_type'],
            'derivation': ref['derivation'], 'evidence_values': ref.get('facts', []),
            'document_id': task['document_id'], 'split': 'train'})
        bag = Counter(tokens(task['question'])); length = sum(bag.values()); lengths.append(length)
        for token, count in bag.items(): postings[token].append([i, count])
    return {'entries': entries, 'postings': dict(postings), 'lengths': lengths,
        'mean_length': sum(lengths) / max(1, len(lengths)), 'algorithm': 'BM25 k1=1.2 b=0.75'}


def retrieve(index, question, count=3):
    scores = defaultdict(float); n = len(index['entries']); avg = index['mean_length']
    for token in set(tokens(question)):
        posting = index['postings'].get(token, []); df = len(posting)
        idf = math.log(1 + (n - df + .5) / (df + .5))
        for i, freq in posting:
            length = index['lengths'][i]
            scores[i] += idf * freq * 2.2 / (freq + 1.2 * (.25 + .75 * length / max(1., avg)))
    ranking = sorted(scores, key=lambda i: (-scores[i], index['entries'][i]['task_id']))[:count]
    return [{**index['entries'][i], 'retrieval_score': scores[i]} for i in ranking]


def main():
    root = ROOT / 'tatdqa/train'; dest = OUT / 'tat_training_memory'
    public = root / 'public/tasks.json'; reference = root / 'scoring_only/references.json'
    tasks = json.loads(public.read_text()); refs = json.loads(reference.read_text())
    if len(tasks) != 13251: raise RuntimeError('Official training question count changed')
    index = create_index(tasks, refs)
    save(dest / 'index.json', index)
    save(dest / 'manifest.json', {'dataset': 'TAT-DQA', 'split': 'train', 'examples': len(tasks),
        'public_training_questions_sha256': sha(public), 'training_supervision_sha256': sha(reference),
        'index_sha256': sha(dest / 'index.json'), 'code_sha256': sha(__file__),
        'development_or_test_answers_opened': False, 'human_confirmations': 0,
        'static_training_examples_not_online_adaptation': True, 'retrieval_uses_public_question_only': True,
        'training_region_labels_not_exported_to_memory': True})
    print('Prepared official training memory:', len(tasks), 'examples')


def semantic_retrieval():
    import numpy as np
    import torch
    from pilot_features import encode_texts, TEXT_MODEL
    torch.set_num_threads(2)
    dest = OUT / 'tat_training_memory'; index_path = dest / 'index.json'
    meta = json.loads((dest / 'manifest.json').read_text())
    if sha(index_path) != meta['index_sha256']: raise RuntimeError('Frozen training memory changed')
    index = json.loads(index_path.read_text()); entries = index['entries']
    public_path = ROOT / 'tatdqa/dev/public/tasks.json'
    tasks = json.loads(public_path.read_text())
    texts = sorted({e['question'] for e in entries} | {q['question'] for q in tasks})
    embeddings = encode_texts(texts)
    matrix = np.stack([embeddings[e['question']] for e in entries]); np.save(dest / 'training_question_embeddings.npy', matrix)
    retrieval = []
    ids = {e['task_id']: i for i, e in enumerate(entries)}
    for task in sorted(tasks, key=lambda q: q['task_id']):
        lexical = retrieve(index, task['question'], 50)
        similarity = matrix @ embeddings[task['question']]
        semantic = sorted(range(len(entries)), key=lambda i: (-float(similarity[i]), entries[i]['task_id']))[:50]
        scores = defaultdict(float)
        for ranking in ([ids[e['task_id']] for e in lexical], semantic):
            for rank, i in enumerate(ranking): scores[i] += 1 / (60 + rank + 1)
        selected = sorted(scores, key=lambda i: (-scores[i], entries[i]['task_id']))[:3]
        retrieval.append({'task_id': task['task_id'], 'examples': [{**entries[i],
            'reciprocal_rank_score': scores[i], 'cosine_similarity': float(similarity[i])} for i in selected]})
    save(dest / 'development_retrieval.json', retrieval)
    save(dest / 'retrieval_manifest.json', {'retrieval': 'BM25 plus frozen MiniLM semantic retrieval; reciprocal rank fusion k=60',
        'training_examples': len(entries), 'development_questions': len(tasks), 'examples_per_question': 3,
        'development_public_questions_sha256': sha(public_path), 'training_index_sha256': sha(index_path),
        'retrieval_sha256': sha(dest / 'development_retrieval.json'),
        'training_embeddings_sha256': sha(dest / 'training_question_embeddings.npy'),
        'semantic_encoder_files': {f.name: sha(f) for f in TEXT_MODEL.iterdir() if f.is_file()},
        'no_development_or_test_reference_answers_opened': True, 'human_confirmations': 0,
        'same_retrieval_artifact_available_to_all_matched_controls': True,
        'not_a_complete_temporal_strong_memory_replication': True, 'code_sha256': sha(__file__)})
    print('Frozen common training-example retrieval:', len(tasks), 'development questions')


if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--stage',choices=('index','semantic'),default='index')
    if parser.parse_args().stage=='semantic': semantic_retrieval()
    else: main()
