"""Two-call native-image/native-OCR development program agent.

The second call sees actual external execution or parser failure and can revise.
No scoring labels, published answer regions, or test documents are opened.
Program validity and claimed visual grounding are reported separately.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

from openai import OpenAI
from native_baseline import public_messages, balance, MODEL
from evidence_program import execute, scope_warnings
from prepare import OUT, save, sha
from public_benchmarks import ROOT
import httpx

SCHEMA = '''Return only a JSON program with exactly these fields:
{"evidence":[{"id":"e0","page":0,"box":[0.1,0.1,0.2,0.2],"text":"exact visible answer span or numeric glyph","value":"signed decimal or null","scope":{"row":"visible row meaning or unknown","period":"visible period or unknown","unit":"visible unit or unknown","scale":"visible scale or unknown","entity":"visible entity or unknown","dimension":"visible dimension or unknown","version":"visible version or unknown"}}],"operation":"arithmetic or extract","expression":"e0/e1*100 or empty string","answer_refs":[],"scale":""}
For arithmetic, reference numeric evidence IDs; allow + - * / parentheses, sum(e0,e1), mean(e0,e1), min(e0,e1), max(e0,e1), count(e0,e1), and constants 0, 1, 100 only. Count can reference nonnumeric visible spans too. No arbitrary code. Keep every observed numeric operand in evidence. For extraction, expression is empty and answer_refs lists evidence IDs whose text is the answer. Do not add labels or explanations to an answer span. The answer scale is one of "", "percent", "thousand", "million", "billion". Numeric value excludes commas/currency/percent signs; parentheses denote a negative number. Use the printed magnitude; percent glyph 25% has value 25. Preserve printed precision for extraction. Page indices are zero-based. Boxes use normalized image coordinates and must enclose the cited span. Maximum 24 evidence spans. Unknown scope must remain unknown. Never infer a date, dimension or entity from an answer reference. A box passing geometric checks does not certify that its content is correct.'''
FIRST = 'Use the original report images and the shared OCR to answer the question with grounded evidence and an executable calculation. OCR can be wrong; check the pixels. ' + SCHEMA
SECOND = 'Review the first program against the same images, question, and external execution below. Correct missing/wrong evidence, period, signs, units, scales, and arithmetic. Return the full final program, even when unchanged. ' + SCHEMA


def ocr_context(task, root):
    manifest = json.loads((root / 'common_perception' / task['document_id'] / 'manifest.json').read_text())
    lines = []
    for index, page in enumerate(manifest['pages']):
        path = root / page['ocr']
        if sha(path) != page['ocr_sha256']:
            raise RuntimeError('Frozen shared OCR changed')
        words = json.loads(path.read_text())['words']
        lines.append('PAGE ' + str(index) + ' OCR words (text, normalized box):\n' + json.dumps(
            [[w['text'], w['box']] for w in words], ensure_ascii=False, separators=(',', ':')))
    text = '\n'.join(lines)
    if len(text) > 160000:
        raise RuntimeError('Shared OCR exceeds registered input budget')
    return text, len(manifest['pages'])


def run(workers=2, limit=0):
    root = ROOT / 'tatdqa/dev'
    tasks = sorted(json.loads((root / 'public/tasks.json').read_text()), key=lambda q: q['task_id'])
    if limit: tasks = tasks[:limit]
    folder = OUT / ('tat_dev_program_smoke' if limit else 'tat_dev_program_agent')
    key = os.environ.get('DEEPSEEK_API_KEY') or os.environ.get('BIRD_AGENT_API_KEY')
    if not key: raise RuntimeError('Dedicated DeepSeek credential missing')
    design = {'method': 'two-call program agent', 'model': MODEL, 'split': 'official development',
        'visual_input': 'all full report page images; detail high', 'ocr_input': 'shared native Tesseract words/boxes',
        'calls_per_question': 2, 'temperature': 0, 'thinking': 'disabled', 'max_output_tokens_per_call': 2048,
        'max_transport_attempts_per_call': 2, 'format_failures_receive_no_extra_calls': True,
        'first_prompt': FIRST, 'second_prompt': SECOND, 'expected_questions': len(tasks),
        'public_tasks_sha256': sha(root / 'public/tasks.json'),
        'common_perception_sha256': sha(root / 'common_perception/manifest.json'),
        'runner_sha256': sha(__file__), 'executor_sha256': sha(Path(__file__).with_name('evidence_program.py')),
        'reference_annotations_opened': False, 'human_confirmations': 0, 'not_ontology_method_advantage_evidence': True,
        'native_baseline_has_different_call_and_OCR_budget': True}
    path = folder / 'design.json'
    if path.exists() and json.loads(path.read_text()) != design:
        raise RuntimeError('Frozen agent design changed')
    save(path, design)
    quota = httpx.Client(timeout=30); available, initial = balance(quota, key)
    if not available or initial < 10: raise RuntimeError('Insufficient available API balance')
    client = OpenAI(api_key=key, base_url='https://api.deepseek.com', timeout=120, max_retries=0)

    def infer(task):
        ident = task['task_id']; target = folder / 'responses' / (ident + '.json')
        if target.exists(): return json.loads(target.read_text())
        messages, images = public_messages(task, root)
        text, pages = ocr_context(task, root)
        messages[0]['content'][0]['text'] = FIRST + '\nQuestion: ' + task['question'] + '\n' + text
        turns = []
        for turn in range(2):
            raw_path = folder / 'raw' / f'{ident}_{turn}.json'
            if raw_path.exists():
                raw = json.loads(raw_path.read_text())
            else:
                raw = None
                for attempt in range(2):
                    marker = folder / 'attempts' / f'{ident}_{turn}_{attempt}.json'
                    if marker.exists():
                        previous = json.loads(marker.read_text())
                        if previous['status'] == 'transport_failure': continue
                        raise RuntimeError('Interrupted API attempt requires uniform recovery review')
                    save(marker, {'status': 'started', 'utc': datetime.now(timezone.utc).isoformat()})
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
                        failure = {'status': 'transport_failure' if retryable else 'terminal_api_failure',
                            'http_status': status, 'error_type': type(exc).__name__, 'error': str(exc).replace(key, '[REDACTED]')[:1000]}
                        save(marker, failure)
                        if not retryable or attempt == 1:
                            record = {'task_id': ident, **failure, 'answer': None, 'turns_completed': len(turns)}
                            save(target, record); return record
                if raw is None:
                    record = {'task_id': ident, 'status': 'transport_attempt_budget_exhausted', 'answer': None}
                    save(target, record); return record
            try:
                if raw['finish_reason'] != 'stop': raise ValueError('Incomplete program generation')
                program = json.loads(raw['content']); result = execute(program, pages)
                warnings = scope_warnings(program)
                feedback = {'execution': result, 'scope_component_warnings': warnings}
            except (ValueError, TypeError, SyntaxError, KeyError) as exc:
                program = None; result = None; feedback = {'execution_error': str(exc)[:500]}
            turns.append({'raw': str(raw_path.relative_to(folder)), 'execution_feedback': feedback})
            if turn == 0:
                messages.append({'role': 'assistant', 'content': raw['content'] or ''})
                messages.append({'role': 'user', 'content': SECOND + '\nExternal execution: ' + json.dumps(feedback, ensure_ascii=False)})
        record = {'task_id': ident, 'status': 'completed' if result else 'invalid_final_program',
            'answer': result['answer'] if result else None, 'program': program, 'turns': turns, 'images': images,
            'visual_grounding_independently_certified': False, 'semantic_scope_independently_certified': False}
        save(target, record); return record
    rows = []; started = time.monotonic()
    try:
        # Bounded batches permit account checks; all selected questions remain in the denominator.
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for start in range(0, len(tasks), 40):
                available, current = balance(quota, key)
                save(folder / 'account_probe.json', {'initial_balance_cny': initial, 'current_balance_cny': current,
                    'debit_includes_other_concurrent_work': True})
                if not available or current < 10 or initial - current > 50: break
                for future in as_completed([pool.submit(infer, q) for q in tasks[start:start+40]]):
                    rows.append(future.result())
                    save(folder / 'progress.json', {'completed_records': len(rows), 'expected_questions': len(tasks),
                        'seconds': time.monotonic() - started})
                    if len(rows) % 10 == 0: print('Program agent', len(rows), '/', len(tasks), flush=True)
        save(folder / 'outcomes.json', sorted(rows, key=lambda r: r['task_id']))
        save(folder / 'inference_manifest.json', {'complete': len(rows) == len(tasks), 'expected_tasks': len(tasks),
            'written_records': len(rows), 'design_sha256': sha(path), 'outcomes_sha256': sha(folder / 'outcomes.json'),
            'scoring_not_run': True, 'public_inputs_only': True})
    finally:
        client.close(); quota.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--limit', type=int, default=0); args = parser.parse_args()
    if not 1 <= args.workers <= 2 or args.limit < 0: parser.error('Workers 1 to 2; nonnegative limit')
    run(args.workers, args.limit)
