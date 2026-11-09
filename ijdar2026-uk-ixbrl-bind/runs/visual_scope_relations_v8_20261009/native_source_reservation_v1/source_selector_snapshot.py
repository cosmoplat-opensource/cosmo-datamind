"""Label-free source reservation; not a certified independent test split.

Uses only complete, stably paginated public repository metadata. Native report
renderability, company overlap, taxonomy/source quality and template duplicates
remain to be audited. No method predictions or report fact labels are read.
"""
from collections import defaultdict
import hashlib
import json
from pathlib import Path
from prepare import save, sha, OUT

INDEX = OUT.parents[1] / 'ext/xbrl_scope_source_index'
TARGET = OUT / 'native_source_reservation_v1'
KNOWN = {'213800LAZNPXSBKVHR17', 'WZE1WSENV6JSZFK0JC28'}


def key(text):
    return hashlib.sha256(('native-scope-source-reservation-v1|' + text).encode()).hexdigest()


def main():
    files = sorted(INDEX.glob('gb_stable_page_*.json'), key=lambda p: int(p.stem.split('_')[-1]))
    docs = [json.loads(p.read_text()) for p in files]
    counts = {d['meta']['count'] for d in docs}
    if len(counts) != 1:
        raise RuntimeError('Public index population changed during pagination')
    declared = next(iter(counts)); records = [r for d in docs for r in d['data']]
    ids = [int(r['id']) for r in records]
    if len(ids) != declared or len(set(ids)) != declared or ids != sorted(ids):
        raise RuntimeError('Stable source index incomplete, duplicated or not strictly ordered')
    entities = {}
    for d in docs:
        for e in d.get('included', []):
            if e['type'] != 'entity':
                continue
            old = entities.get(e['id'])
            if old and old != e['attributes']:
                raise RuntimeError('Entity metadata changed during source snapshot')
            entities[e['id']] = e['attributes']
    grouped = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for r in records:
        attr = r['attributes']; period = attr['period_end']
        if period[:4] not in ('2023', '2024', '2025'):
            continue
        entity_id = r['relationships']['entity']['data']['id']
        if entity_id not in entities:
            raise RuntimeError('Referenced source entity metadata missing')
        lei = entities[entity_id]['identifier']
        if lei in KNOWN:
            continue
        if not all(attr.get(k) for k in ('report_url', 'json_url', 'package_url')):
            continue
        grouped[entity_id][period[5:]][period[:4]].append(r)
    eligible = []
    for eid, months in grouped.items():
        annual = [md for md, years in months.items() if set(years) >= {'2023', '2024', '2025'}]
        if not annual:
            continue
        entity = entities[eid]; lei = entity['identifier']
        month = min(annual, key=lambda md: key(lei + '|' + md))
        reports = []
        for year in ('2023', '2024', '2025'):
            choices = months[month][year]
            # This specifies a source version deterministically. It does NOT
            # certify that repository date/id is the actual filing chronology.
            chosen = min(choices, key=lambda r: key(lei + '|' + r['id']))
            reports.append({'filing_id': chosen['id'], 'attributes': chosen['attributes'],
                            'same_entity_period_versions_in_index': len(choices)})
        eligible.append({'entity_id': eid, 'lei': lei, 'name': entity['name'], 'period_month_day': month,
                         'source_order_key': key(lei), 'reports': reports})
    eligible.sort(key=lambda e: e['source_order_key'])
    if len(eligible) < 84:
        raise RuntimeError('Insufficient complete metadata series for the source reservation')
    for index, entity in enumerate(eligible):
        entity['tentative_role'] = ('additional_development' if index < 16 else
            'calibration' if index < 24 else 'reserved_independent_test' if index < 84 else 'ordered_spare_queue')
    artifact = {'entities': eligible, 'metadata_only': True, 'final_test_split_not_certified': True,
        'companies_and_templates_overlap_audit_pending': True, 'report_quality_and_renderability_pending': True,
        'actual_filing_chronology_not_assumed': True, 'selection_uses_method_outputs_or_fact_labels': False,
        'population': 'GB repository reports with same fiscal month/day in all of 2023, 2024, 2025',
        'counts': {'index_filings': declared, 'eligible_company_series': len(eligible),
                   'additional_development': 16, 'calibration': 8, 'reserved_independent_test': 60,
                   'ordered_spares': len(eligible) - 84},
        'quality_replacements_must_follow_frozen_spare_queue_before_results': True,
        'source_index_hashes': [{'file': p.name, 'sha256': sha(p)} for p in files],
        'code_sha256': sha(__file__)}
    path = TARGET / 'metadata_reservation.json'
    if path.exists() and json.loads(path.read_text()) != artifact:
        raise RuntimeError('Frozen metadata source reservation changed')
    save(path, artifact)
    (TARGET / 'source_selector_snapshot.py').write_bytes(Path(__file__).read_bytes())
    print(json.dumps(artifact['counts'], indent=2))
    print('First development entity:', eligible[0]['name'], eligible[0]['lei'])


if __name__ == '__main__':
    main()
