"""One shared DeepSeek visual interpretation for each assisted target batch.

Reads public diagnostic tasks only. Original full pages and the frozen observed
candidate pool are identical for all subsequent controls. No answer feedback,
publisher values, hidden tags or scoring files enter these model requests.
"""
import base64
from collections import defaultdict
import json
import os
from pathlib import Path
import time

import httpx
from openai import OpenAI
from PIL import Image

from canonicalization_prepare import DEST
from native_baseline import MODEL, balance
from prepare import opaque, save, sha

PROMPT = (
    'This is a diagnostic of displayed financial quantities, with field identity, '
    'target rectangle and unit multiplier supplied equally to every method. '
    'Read the original page and identify the literal row and presentation context '
    'of each target. Map its displayed numeric value to the supplied canonical '
    'field definition, using only a listed candidate. Parentheses encode a display '
    'negative, but expense or outflow fields may require inversion whereas net '
    'gain/loss fields can retain that sign. Debit/credit metadata alone is not a '
    'universal sign rule: notes, reconciliations, and net fields may differ. '
    'Preserve unknown cases. Do not invent absent field declarations or quantities. '
    'The numeric candidates come from fixed public OCR; if none is defensible, '
    'choose null. No reference answer or publisher transform is supplied. '
    'Return JSON {"targets":[{"task_id":"...","candidate_index":0 or null,'
    '"row_text":"literal visible row label","section_text":"literal visible section label",'
    '"presentation":"income_statement|cashflow_statement|balance_sheet|equity_change|note|other|unknown",'
    '"uncertain":true}]}. Indices are zero based. Include every requested target once. '
    'Scope hypotheses supplied below are shared model observations, not truth.\n')

PRESENTATIONS = {'income_statement', 'cashflow_statement', 'balance_sheet',
                 'equity_change', 'note', 'other', 'unknown'}


def request_targets(tasks):
    return [{key: task[key] for key in (
        'task_id', 'target_box', 'declared_field', 'field_properties',
        'oracle_multiplier', 'ocr_strings', 'public_row_text',
        'public_top_text', 'candidates')} for task in tasks]


def validate(raw, tasks):
    data = json.loads(raw)
    rows = data.get('targets') if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError('Missing targets array')
    expected = {t['task_id']: t for t in tasks}
    if len(rows) != len(expected) or {r.get('task_id') for r in rows} != set(expected):
        raise ValueError('Missing or duplicate requested targets')
    for row in rows:
        index = row.get('candidate_index')
        if index is not None and (type(index) is not int or not 0 <= index < len(expected[row['task_id']]['candidates'])):
            raise ValueError('Candidate outside common program pool')
        if row.get('presentation') not in PRESENTATIONS:
            raise ValueError('Unsupported visual presentation class')
        if not isinstance(row.get('row_text'), str) or not isinstance(row.get('section_text'), str):
            raise ValueError('Missing public text evidence')
        if type(row.get('uncertain')) is not bool:
            raise ValueError('Ambiguity must be explicit')
    return rows


def run(limit=0):
    tasks_file = DEST / 'public/tasks.json'
    protocol_file = DEST / 'protocol.json'
    protocol = json.loads(protocol_file.read_text())
    if sha(tasks_file) != protocol['public_tasks_sha256']:
        raise RuntimeError('Frozen public diagnostic inputs changed')
    tasks = json.loads(tasks_file.read_text())
    grouped = defaultdict(list)
    for task in tasks:
        grouped[task['page_id']].append(task)
    batches = []
    for page_id, items in sorted(grouped.items()):
        items.sort(key=lambda t: t['task_id'])
        for offset in range(0, len(items), 10):
            group = items[offset:offset + 10]
            batches.append((opaque([page_id, [t['task_id'] for t in group]]), group))
    design = {'model': MODEL, 'temperature': 0, 'thinking': 'disabled',
              'image_detail': 'high', 'max_output_tokens': 2000,
              'max_attempts_per_batch': 2, 'maximum_targets_per_batch': 10,
              'tasks': len(tasks), 'logical_batches': len(batches), 'prompt': PROMPT,
              'public_tasks_sha256': sha(tasks_file),
              'private_numeric_reference_or_publisher_signs_read': False,
              'answers_and_past_feedback_read': False,
              'oracle_region_field_scale_shared_by_all_controls': True,
              'all_controls_reuse_exact_same_raw_observations': True,
              'human_confirmations': 0, 'code_sha256': sha(__file__)}
    dest = DEST / 'common_visual'; design_path = dest / 'design.json'
    if design_path.exists() and json.loads(design_path.read_text()) != design:
        raise RuntimeError('Frozen common diagnostic model design changed')
    save(design_path, design)
    key = os.environ.get('DEEPSEEK_API_KEY') or os.environ.get('BIRD_AGENT_API_KEY')
    if not key:
        raise RuntimeError('Dedicated DeepSeek credential missing')
    client = OpenAI(api_key=key, base_url='https://api.deepseek.com', timeout=90, max_retries=0)
    quota = httpx.Client(timeout=30)
    available, initial = balance(quota, key)
    if not available or initial < 10:
        raise RuntimeError('Insufficient available DeepSeek balance')
    completed = []
    try:
        for i, (ident, group) in enumerate(batches):
            if limit and i >= limit:
                break
            file = dest / 'batches' / (ident + '.json')
            if file.exists():
                record = json.loads(file.read_text())
                if record['task_ids'] != [t['task_id'] for t in group]:
                    raise RuntimeError('Frozen batch identity changed')
                completed.append(record); continue
            if i % 25 == 0:
                available, current = balance(quota, key)
                save(dest / 'account_probe.json', {'current_balance_cny': current,
                    'debit_includes_concurrent_work': True})
                if not available or current < 10 or initial - current > 60:
                    break
            image = Path(group[0]['image'])
            if sha(image) != group[0]['image_sha256']:
                raise RuntimeError('Original diagnostic pixels changed')
            with Image.open(image) as im:
                mime = Image.MIME[im.format]
            prompt = PROMPT + json.dumps({'targets': request_targets(group),
                       'shared_scope_observations': group[0]['shared_headers']}, ensure_ascii=False)
            result = None
            for attempt in (1, 2):
                marker = dest / 'attempts' / f'{ident}_{attempt}.json'
                raw_file = dest / 'raw' / f'{ident}_{attempt}.json'
                if raw_file.exists():
                    raw = json.loads(raw_file.read_text())
                elif marker.exists():
                    continue
                else:
                    save(marker, {'status': 'started', 'task_ids': [t['task_id'] for t in group]})
                    start = time.monotonic()
                    try:
                        reply = client.chat.completions.create(model=MODEL, temperature=0, max_tokens=2000,
                            response_format={'type': 'json_object'}, extra_body={'thinking': {'type': 'disabled'}},
                            messages=[{'role': 'user', 'content': [{'type': 'text', 'text': prompt},
                                {'type': 'image_url', 'image_url': {'url': 'data:' + mime + ';base64,'
                                    + base64.b64encode(image.read_bytes()).decode(), 'detail': 'high'}}]}])
                        raw = {'content': reply.choices[0].message.content,
                               'finish_reason': reply.choices[0].finish_reason, 'returned_model': reply.model,
                               'usage': reply.usage.model_dump() if reply.usage else {},
                               'seconds': time.monotonic() - start}
                        save(raw_file, raw); save(marker, {'status': 'response_saved'})
                    except Exception as exc:
                        status = getattr(exc, 'status_code', None)
                        save(dest / 'failures' / f'{ident}_{attempt}.json', {
                            'error_type': type(exc).__name__, 'http_status': status,
                            'error': str(exc).replace(key, '[REDACTED]')[:800]})
                        if status in (400, 401, 402, 403):
                            raise RuntimeError('Configuration/account error; diagnostic paused') from exc
                        continue
                try:
                    if raw['finish_reason'] != 'stop':
                        raise ValueError('Incomplete diagnostic observation')
                    result = {'status': 'completed', 'targets': validate(raw['content'], group),
                              'raw_response_sha256': sha(raw_file), 'returned_model': raw['returned_model']}
                    break
                except (ValueError, TypeError, KeyError) as exc:
                    save(dest / 'failures' / f'{ident}_{attempt}_format.json', {'error': str(exc)[:500]})
            if result is None:
                result = {'status': 'attempt_budget_exhausted', 'targets': []}
            result.update(batch_id=ident, task_ids=[t['task_id'] for t in group],
                          image_sha256=group[0]['image_sha256'])
            save(file, result); completed.append(result)
            save(dest / 'progress.json', {'processed_batches': len(completed), 'expected_batches': len(batches),
                'completed_batches': sum(r['status'] == 'completed' for r in completed)})
            print('Shared polarity diagnostic', len(completed), '/', len(batches), result['status'], flush=True)
        save(dest / 'manifest.json', {'complete': len(completed) == len(batches),
            'records': completed, 'expected_batches': len(batches), 'processed_batches': len(completed),
            'design_sha256': sha(design_path), 'public_tasks_sha256': sha(tasks_file),
            'private_reference_or_feedback_files_read': False,
            'no_new_scope_ownership_gold_created': True})
    finally:
        client.close(); quota.close()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(); parser.add_argument('--limit', type=int, default=0)
    run(parser.parse_args().limit)
