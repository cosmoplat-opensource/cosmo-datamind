"""Confirmed deterministic extraction and source-located failures — DR-070."""
from . import _extractor, _schema
from .common import ModelingError, checked_tbox, digest
from .evidence import source_digests


def validate_result(result, tbox, evidence_rows):
    return _schema.validate_result(result, checked_tbox(tbox), evidence_rows, set())


def extract(confirmation, evidence_bundle):
    sources = source_digests(evidence_bundle)
    if (not isinstance(confirmation, dict) or not isinstance(confirmation.get('tbox'), dict)
            or confirmation.get('sha256') != digest(confirmation['tbox'])
            or confirmation.get('source_sha256') != sources
            or not confirmation.get('vocabulary_sha256')):
        raise ModelingError('confirmation_required', '骨架尚未确认，或骨架／来源已发生变化。')
    schema = checked_tbox(confirmation['tbox'])
    evidence = evidence_bundle['evidence']
    try:
        result, report = _extractor.build_result(schema, evidence)
    except _extractor.ExtractionError as exc:
        documents = {row['id']: row for row in evidence_bundle['documents']}
        by_id = {row['id']: row for row in evidence}
        issues = []
        for ref in exc.source_refs:
            for loc in by_id[ref]['locations']:
                issues.append({'code': 'unsupported_source', 'message': str(exc), 'source_ref': ref,
                    'source_role': documents[loc['document_id']]['role'],
                    'location': {'document_id': loc['document_id'], 'path': loc['path']}})
        raise ModelingError('unsupported_source', str(exc), issues=issues) from None
    checked = _schema.validate_result(result, schema, evidence, set())
    return {'result': checked, 'report': report}
