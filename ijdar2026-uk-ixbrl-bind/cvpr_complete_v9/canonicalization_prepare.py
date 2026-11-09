"""E0 display-direction isolation on a pre-reserved development cohort.

All controls receive identical oracle field identity, rectangle and publisher
unit multiplier. Only the public pixels/OCR supply the displayed quantity.
This is deliberately an assisted diagnostic, not a native QA main result.
Reference quantities do not determine selection. No future labels train models.
"""
from collections import Counter
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re

from lxml import etree

from prepare import EXP, OUT, opaque, save, sha
from public_schema import lookup
from qualify_teacher_cells import overlap_fraction, public_number_candidates

ROOT = OUT / 'expanded_development_sources'
DEST = OUT / 'canonicalization_isolation_v1'
PER_REPORT = 20


def valid_box(box):
    return (isinstance(box, list) and len(box) == 4
            and all(type(x) in (int, float) and 0 <= x <= 1 for x in box)
            and box[0] < box[2] and box[1] < box[3])


def public_scopes(headers):
    """Invalid or missing scope regions remain unknown, not whole-page scopes."""
    result = []
    for header in headers:
        if not isinstance(header, dict) or not valid_box(header.get('box')):
            continue
        regions = header.get('applies_to', [])
        if not isinstance(regions, list):
            regions = []
        valid = [r for r in regions if valid_box(r)]
        result.append({'text': header['text'], 'kind': header['kind'],
                       'box': header['box'], 'scope_candidates': valid,
                       'scope_unknown': not valid or len(valid) != len(regions),
                       'ambiguous': header.get('ambiguous') is not False,
                       'independently_certified': False})
    return result


def nearest_public_text(words, box):
    """Same-row left text and top-of-page context, without source DOM labels."""
    cy = (box[1] + box[3]) / 2
    row = [w for w in words if abs((w['box'][1] + w['box'][3]) / 2 - cy) < .006
           and w['box'][0] < box[0] - .01]
    row.sort(key=lambda w: w['box'][0])
    top = [w for w in words if w['box'][1] < min(box[1], .18)]
    return ' '.join(w['text'] for w in row), ' '.join(w['text'] for w in top)[:3000]


def namespaces(path):
    iterator = etree.iterparse(str(path), events=('start',),
                               resolve_entities=False, no_network=True, huge_tree=True)
    _, root = next(iterator)
    return dict(root.nsmap)


def ordered_prefix(tasks, company, budget):
    past = [t for t in tasks if t['company'] == company and t['source_year'] == 2023]
    return sorted(past, key=lambda t: t['task_id'])[:budget]


def make_candidates(texts, multiplier):
    joined = ' '.join(texts)
    candidates = set(public_number_candidates(joined))
    if not candidates:
        for text in texts:
            candidates.update(public_number_candidates(text))
    result = []
    for literal in sorted(candidates):
        for polarity in (1, -1):
            result.append({'literal': literal, 'polarity': polarity,
                           'computed_answer': str(Decimal(literal) * Decimal(multiplier) * polarity)})
    return result


def prepare():
    if (DEST / 'protocol.json').exists():
        raise RuntimeError('Diagnostic selection already frozen')
    schema_file = ROOT / 'public_schema/ifrs_20220324_properties.json'
    catalog = json.loads(schema_file.read_text())
    plan_file = EXP / 'runs/visual_scope_relations_v8_20261009/scope_boundary_development_v1/download_plan.json'
    plan = json.loads(plan_file.read_text())
    files = {(r['lei'], r['period_end'][:4]): Path(r['path'])
             for r in plan['files'] if r['kind'] == 'native_report'}
    rendering_file = ROOT / 'native_renders_v3/render_manifest_dpr2.json'
    rendering = json.loads(rendering_file.read_text())
    pages_file = ROOT / 'public_feature_pages.json'
    pages = {(p['report_id'], p['page_index']): p for p in json.loads(pages_file.read_text())}
    ocr_file = ROOT / 'common_perception/manifest.json'
    observations = {(r['report'], r['page']): r for r in json.loads(ocr_file.read_text())['pages']}
    tasks, references, feedback, report_records = [], {}, {}, []
    counts = Counter()
    input_hashes = {str(p): sha(p) for p in (schema_file, plan_file, rendering_file, pages_file, ocr_file)}
    for report in rendering:
        company = report['entity']; year = int(report['period_end'][:4])
        ident = company + '_' + str(year)
        ns = namespaces(files[company, str(year)])
        audit_path = ROOT / 'scoring_only' / (company + '_' + report['period_end'] + '.json')
        audit = json.loads(audit_path.read_text()); input_hashes[str(audit_path)] = sha(audit_path)
        facts = {r['region_id']: r for r in audit['rows'] if r['region_id']}
        available = []
        for index, page in enumerate(report['selected_pages']):
            public_page = pages[ident, index]
            observation = observations[ident, index]
            path = Path(observation['ocr_path'])
            if sha(path) != observation['ocr_sha256']:
                raise RuntimeError('Frozen OCR changed')
            ocr = json.loads(path.read_text()); width, height = ocr['width'], ocr['height']
            header_path = ROOT / 'common_headers/headers' / (public_page['page_id'] + '.json')
            header_record = json.loads(header_path.read_text())
            headers = public_scopes(header_record.get('observation', {}).get('headers', []))
            for region in page['scoring_only_region_boxes']:
                fact = facts.get(region['id']); counts['rendered_regions'] += 1
                if not fact or not fact['published_fact_found']:
                    counts['unscorable_reference_missing'] += 1; continue
                try:
                    multiplier = str(Decimal(10) ** int(fact['scale']))
                    value = str(Decimal(str(fact['canonical_value'])))
                except (ValueError, TypeError, InvalidOperation):
                    counts['unscorable_invalid_numeric_reference'] += 1; continue
                x, y, dx, dy = region['bbox']
                box = [max(0, x / width), max(0, y / height), min(1, (x + dx) / width), min(1, (y + dy) / height)]
                if not valid_box(box):
                    counts['invalid_region_geometry'] += 1; continue
                padded = [x - 12, y - 12, x + dx + 12, y + dy + 12]
                words = [w for w in ocr['words'] if overlap_fraction(padded, [v * (width if j % 2 == 0 else height)
                              for j, v in enumerate(w['box'])]) >= .5]
                words.sort(key=lambda w: (round(w['box'][1] / .005), w['box'][0]))
                texts = [w['text'] for w in words]
                qname = fact['concept']; prefix, _, name = qname.partition(':')
                properties = lookup(catalog, ns.get(prefix), name)
                row_text, top_text = nearest_public_text(ocr['words'], box)
                task_id = opaque(json.dumps([public_page['image_sha256'], box, qname], sort_keys=True))
                task = {'task_id': task_id, 'company': company, 'report': ident,
                        'source_year': year, 'page_id': public_page['page_id'],
                        'image': public_page['image'], 'image_sha256': public_page['image_sha256'],
                        'target_box': box, 'declared_field': qname, 'field_properties': properties,
                        'oracle_multiplier': multiplier, 'ocr_strings': texts,
                        'public_row_text': row_text, 'public_top_text': top_text,
                        'shared_headers': headers, 'header_inventory_status': header_record['status'],
                        'candidates': make_candidates(texts, multiplier),
                        'track': 'oracle_region_field_and_unit_multiplier_display_direction_only'}
                reference = {'task_id': task_id, 'canonical_value': value,
                             'canonical_unit': fact['canonical_dimensions'].get('unit'),
                             'publisher_region_id': region['id'], 'audit_file': str(audit_path),
                             'not_independently_certified_full_semantic_binding': True}
                available.append((task, reference))
        # No reference answer, sign, transform agreement, OCR success, or model
        # success enters this public hash ordering.
        available.sort(key=lambda pair: pair[0]['task_id'])
        chosen = available[:PER_REPORT]
        if len({t['task_id'] for t, _ in chosen}) != len(chosen):
            raise RuntimeError('Duplicate assisted task')
        for task, reference in chosen:
            tasks.append(task); references[task['task_id']] = reference
            if year == 2023:
                feedback[task['task_id']] = {'task_id': task['task_id'], 'source_year': year,
                                             'answer': reference['canonical_value'],
                                             'unit': reference['canonical_unit']}
        report_records.append({'report': ident, 'eligible_reference_regions': len(available),
                               'selected_public_hash_prefix': len(chosen)})
    if len({t['task_id'] for t in tasks}) != len(tasks):
        raise RuntimeError('Across-report duplicate task')
    save(DEST / 'public/tasks.json', tasks)
    save(DEST / 'training_only/past_feedback.json', feedback)
    save(DEST / 'scoring_only/references.json', references)
    companies = sorted({t['company'] for t in tasks})
    save(DEST / 'feedback_prefixes.json', {
        c: {str(b): [t['task_id'] for t in ordered_prefix(tasks, c, b)] for b in (0, 5, 10)}
        for c in companies})
    save(DEST / 'protocol.json', {
        'stage': 'E0 display-direction isolation; E4 development preparation',
        'selected_tasks': len(tasks), 'companies': len(companies), 'reports': report_records,
        'per_report_public_hash_prefix': PER_REPORT, 'feedback_year_only': 2023,
        'evaluation_years': [2024, 2025], 'automatic_feedback_budgets': [0, 5, 10],
        'human_confirmations': 0, 'oracle_field_region_and_scale_for_all_controls': True,
        'source_reference_quantities_used_for_selection': False,
        'reference_availability_is_an_eligibility_requirement': True,
        'unknown_numeric_parses_and_ambiguous_locales_retained': True,
        'candidate_missing_gold_is_not_excluded_from_evaluation': True,
        'full_visual_scope_ownership_certified': False, 'not_native_public_qa_main_result': True,
        'future_development_labels_previous_quality_audit_only_not_training': True,
        'held_out_or_calibration_sources_accessed': False,
        'private_teacher_scale_is_an_explicit_oracle_input': True,
        'public_field_declarations_shared_identically': True,
        'catalog_only_covers_exact_2022_namespace_other_versions_unknown': True,
        'source_quality_counts': dict(counts), 'preparation_input_hashes': input_hashes,
        'public_tasks_sha256': sha(DEST / 'public/tasks.json'),
        'feedback_prefixes_sha256': sha(DEST / 'feedback_prefixes.json'),
        'runner_sha256': sha(__file__)})
    print('Frozen polarity isolation:', len(tasks), 'tasks,', len(companies), 'companies')


if __name__ == '__main__':
    prepare()
