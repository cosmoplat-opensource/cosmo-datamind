"""Shared-first-stage agent with static TRAIN-example semantic retrieval.

The first tool call is cached identically from the program-agent condition.
One final visual call has the same output budget and execution tools. Cached
common calls are counted logically, with physical API reuse reported separately.
This is a retrieval-agent control, not a claim to replicate temporal A-MEM/Zep.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import time

import httpx
from openai import OpenAI
from evidence_program import execute, scope_warnings
from native_baseline import public_messages, balance, MODEL
from prepare import OUT, save, sha
from public_benchmarks import ROOT
from tat_agent import FIRST, SECOND, ocr_context

MEMORY_RULE = ('\nBelow are three examples retrieved ONLY from the official training split. '
    'Use them to interpret the question and choose a reasoning pattern. Their values, dates, '
    'entities and regions are not evidence for this report. Every operand and final extracted '
    'span must be read from the CURRENT images. Do not copy an example answer.\n')


def common_first(task, parent, page_count, maximum_wait=1800):
    raw_path = parent / 'raw' / f"{task['task_id']}_0.json"
    terminal = parent / 'responses' / f"{task['task_id']}.json"
    deadline = time.monotonic() + maximum_wait
    while not raw_path.exists():
        if terminal.exists():
            row = json.loads(terminal.read_text())
            return None, None, {'task_id': task['task_id'], 'status': 'shared_first_stage_failure',
                'answer': None, 'common_failure': row['status']}
        if time.monotonic() > deadline:
            raise RuntimeError('Common first stage not available; dependent run paused')
        time.sleep(2)
    raw = json.loads(raw_path.read_text())
    try:
        if raw['finish_reason'] != 'stop': raise ValueError('Incomplete program generation')
        program = json.loads(raw['content']); result = execute(program, page_count)
        feedback = {'execution': result, 'scope_component_warnings': scope_warnings(program)}
    except (ValueError, TypeError, SyntaxError, KeyError) as exc:
        feedback = {'execution_error': str(exc)[:500]}
    return raw, feedback, None


def run(workers=2, limit=0):
    root = ROOT / 'tatdqa/dev'; parent = OUT / 'tat_dev_program_agent'
    parent_design = json.loads((parent / 'design.json').read_text())
    if parent_design['executor_sha256'] != sha(Path(__file__).with_name('evidence_program.py')):
        raise RuntimeError('Shared first-stage executor changed')
    memory = OUT / 'tat_training_memory'; retrieval_file = memory / 'development_retrieval.json'
    rm = json.loads((memory / 'retrieval_manifest.json').read_text())
    if sha(retrieval_file) != rm['retrieval_sha256']:
        raise RuntimeError('Frozen common retrieval changed')
    retrieved = {r['task_id']: r['examples'] for r in json.loads(retrieval_file.read_text())}
    tasks = sorted(json.loads((root / 'public/tasks.json').read_text()), key=lambda q: q['task_id'])
    if limit: tasks = tasks[:limit]
    folder = OUT / ('tat_dev_memory_smoke' if limit else 'tat_dev_memory_agent')
    design = {'method': 'two-call program agent plus static training semantic retrieval', 'model': MODEL,
        'split': 'official development', 'visual_input': parent_design['visual_input'],
        'ocr_input': parent_design['ocr_input'], 'calls_per_question_logical': 2,
        'new_calls_per_question': 1, 'first_call_cache_shared_with_all_controls': True,
        'temperature': 0, 'thinking': 'disabled', 'max_output_tokens_per_call': 2048,
        'max_transport_attempts_per_call': 2, 'format_failures_receive_no_extra_calls': True,
        'first_prompt': FIRST, 'second_prompt': SECOND, 'memory_rule': MEMORY_RULE,
        'examples_per_question': 3, 'training_memory_not_online_feedback': True,
        'common_first_design_sha256': sha(parent / 'design.json'),
        'training_retrieval_manifest_sha256': sha(memory / 'retrieval_manifest.json'),
        'expected_questions': len(tasks), 'public_tasks_sha256': sha(root / 'public/tasks.json'),
        'common_perception_sha256': sha(root / 'common_perception/manifest.json'),
        'runner_sha256': sha(__file__), 'executor_sha256': sha(Path(__file__).with_name('evidence_program.py')),
        'development_or_test_reference_annotations_opened': False, 'human_confirmations': 0,
        'not_ontology_method_advantage_evidence': True, 'not_full_temporal_memory_replication': True}
    design_path = folder / 'design.json'
    if design_path.exists() and json.loads(design_path.read_text()) != design:
        raise RuntimeError('Frozen retrieval-agent design changed')
    save(design_path, design)
    key = os.environ.get('DEEPSEEK_API_KEY') or os.environ.get('BIRD_AGENT_API_KEY')
    if not key: raise RuntimeError('Dedicated DeepSeek credential missing')
    quota = httpx.Client(timeout=30); available, initial = balance(quota, key)
    if not available or initial < 10: raise RuntimeError('Insufficient available API balance')
    client = OpenAI(api_key=key, base_url='https://api.deepseek.com', timeout=120, max_retries=0)

    def infer(task):
        ident = task['task_id']; target = folder / 'responses' / (ident + '.json')
        if target.exists(): return json.loads(target.read_text())
        messages, images = public_messages(task, root); ocr, pages = ocr_context(task, root)
        first, feedback, failure = common_first(task, parent, pages)
        if failure: save(target, failure); return failure
        examples = retrieved[ident]
        if len(examples) != 3 or any(e['split'] != 'train' for e in examples):
            raise RuntimeError('Invalid shared training-example retrieval')
        # Exclude source IDs and retrieval scores from the model's semantic examples.
        exposed = [{k: e[k] for k in ('question', 'answer', 'scale', 'answer_type', 'derivation', 'evidence_values')} for e in examples]
        example_text = json.dumps(exposed, ensure_ascii=False)
        if len(example_text) > 16000: raise RuntimeError('Retrieved examples exceed registered input budget')
        messages[0]['content'][0]['text'] = FIRST + '\nQuestion: ' + task['question'] + '\n' + ocr
        messages.append({'role': 'assistant', 'content': first['content'] or ''})
        messages.append({'role': 'user', 'content': SECOND + '\nExternal execution: ' + json.dumps(feedback, ensure_ascii=False)
            + MEMORY_RULE + example_text})
        raw_path = folder / 'raw' / (ident + '.json')
        if raw_path.exists(): raw = json.loads(raw_path.read_text())
        else:
            raw = None
            for attempt in range(2):
                marker = folder / 'attempts' / f'{ident}_{attempt}.json'
                if marker.exists():
                    old = json.loads(marker.read_text())
                    if old['status'] == 'transport_failure': continue
                    raise RuntimeError('Interrupted API attempt requires uniform recovery review')
                save(marker, {'status': 'started'})
                started = time.monotonic()
                try:
                    r = client.chat.completions.create(model=MODEL, temperature=0, max_tokens=2048,
                        response_format={'type': 'json_object'}, extra_body={'thinking': {'type': 'disabled'}}, messages=messages)
                    raw = {'content': r.choices[0].message.content, 'finish_reason': r.choices[0].finish_reason,
                        'returned_model': r.model, 'usage': r.usage.model_dump() if r.usage else {},
                        'seconds': time.monotonic() - started}
                    save(raw_path, raw); save(marker, {'status': 'response_saved'}); break
                except Exception as exc:
                    status = getattr(exc, 'status_code', None)
                    retryable = type(exc).__name__ in ('APIConnectionError', 'APITimeoutError') or status in (408, 429, 500, 502, 503, 504)
                    failure = {'task_id': ident, 'status': 'transport_failure' if retryable else 'terminal_api_failure',
                        'http_status': status, 'error_type': type(exc).__name__, 'error': str(exc).replace(key, '[REDACTED]')[:1000], 'answer': None}
                    save(marker, failure)
                    if not retryable or attempt == 1: save(target, failure); return failure
            if raw is None:
                failure = {'task_id': ident, 'status': 'transport_attempt_budget_exhausted', 'answer': None}
                save(target, failure); return failure
        try:
            if raw['finish_reason'] != 'stop': raise ValueError('Incomplete program generation')
            program = json.loads(raw['content']); result = execute(program, pages)
            final_feedback = {'execution': result, 'scope_component_warnings': scope_warnings(program)}
        except (ValueError, TypeError, SyntaxError, KeyError) as exc:
            program = None; result = None; final_feedback = {'execution_error': str(exc)[:500]}
        record = {'task_id': ident, 'status': 'completed' if result else 'invalid_final_program',
            'answer': result['answer'] if result else None, 'program': program, 'images': images,
            'common_first_response_sha256': sha(parent / 'raw' / f'{ident}_0.json'),
            'retrieved_train_question_ids': [e['task_id'] for e in examples],
            'first_execution_feedback': feedback, 'final_execution_feedback': final_feedback,
            'physical_first_call_reused': True, 'visual_grounding_independently_certified': False}
        save(target, record); return record

    rows = []; started = time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for offset in range(0, len(tasks), 40):
                available, current = balance(quota, key)
                save(folder / 'account_probe.json', {'initial_balance_cny': initial, 'current_balance_cny': current,
                    'debit_includes_other_concurrent_work': True})
                if not available or current < 10 or initial - current > 50: break
                for f in as_completed([pool.submit(infer, q) for q in tasks[offset:offset+40]]):
                    rows.append(f.result()); save(folder / 'progress.json', {'completed_records': len(rows),
                        'expected_questions': len(tasks), 'seconds': time.monotonic() - started})
                    if len(rows) % 10 == 0: print('Retrieval agent', len(rows), '/', len(tasks), flush=True)
        save(folder / 'outcomes.json', sorted(rows, key=lambda r: r['task_id']))
        save(folder / 'inference_manifest.json', {'complete': len(rows) == len(tasks), 'expected_tasks': len(tasks),
            'written_records': len(rows), 'design_sha256': sha(design_path), 'outcomes_sha256': sha(folder / 'outcomes.json'),
            'scoring_not_run': True, 'public_inputs_plus_official_training_examples_only': True})
    finally:
        client.close(); quota.close()


if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('--workers',type=int,default=2);p.add_argument('--limit',type=int,default=0)
    args=p.parse_args()
    if not 1<=args.workers<=2 or args.limit<0: p.error('Workers 1 to 2; nonnegative limit')
    run(args.workers,args.limit)
