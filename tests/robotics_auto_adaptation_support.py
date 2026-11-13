"""Real saved designs with a custom source term, no model/network substitution — DR-072."""
from copy import deepcopy

from domains.robotics.modeling import vocabulary
from tests.robotics_design_support import Harness, proposals, reply


def custom_candidate(root, monkeypatch, *, rename=True):
    frozen = vocabulary.load_vocabulary()
    frozen['sha256'] = 'a' * 64
    frozen['version'] = 'synthetic-custom-vocabulary'
    classes, properties = deepcopy(proposals())
    if rename:
        for kind in ('classes', 'object_properties', 'data_properties'):
            for row in frozen[kind]:
                if row['id'] == 'SkillInvocation':
                    row.update(id='CallSite', label='调用点', iri=row['iri'].replace('SkillInvocation', 'CallSite'))
                for field in ('parent_ids', 'domain_ids', 'range_ids', 'class_ids'):
                    if field in row:
                        row[field] = ['CallSite' if name == 'SkillInvocation' else name for name in row[field]]
        for rows in (classes['classes'], properties['object_properties'], properties['data_properties']):
            for row in rows:
                if row['id'] == 'SkillInvocation': row.update(id='CallSite', label='调用点')
                for field in ('parent_ids', 'domain_ids', 'range_ids', 'class_ids'):
                    if field in row:
                        row[field] = ['CallSite' if name == 'SkillInvocation' else name for name in row[field]]
    h = Harness(root)
    with monkeypatch.context() as patch:
        patch.setattr(vocabulary, 'load_vocabulary', lambda: deepcopy(frozen))
        h.runtime.responses.append(reply(classes))
        state = h.submit(description='合成自定义术语：调用点用于表达技能调用')
        h.run(); state = h.get(state)
        state = h.service.confirm_classes(state['session_id'], request_id='confirm-classes',
            expected_revision=state['revision'], snapshot_sha256=state['snapshot_sha256'])
        h.runtime.responses.append(reply(properties)); h.message(state); h.run(); state = h.get(state)
        h.service.confirm_schema(state['session_id'], request_id='confirm-schema',
            expected_revision=state['revision'], snapshot_sha256=state['snapshot_sha256'])
        h.run(); state = h.get(state)
    return h, state, state['output_asset_ref']
