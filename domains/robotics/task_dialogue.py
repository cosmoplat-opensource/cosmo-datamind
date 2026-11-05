"""Replay explicit user choices for the fixed robotics procedure — DR-062 / DR-066.

Adapted from Yang's grounded_mentions; full IRIs preserve duplicate-name identities.
Only business names enter the model catalog, never robot parameters or credentials.
"""
import re

from rdflib import RDF, RDFS

from .graph import S, local_name

ENTITY_FIELDS = {
    'part': ('Workpiece', '零件', r'零部件|零件|工件|物料'),
    'station': ('Workstation', '工位', r'工位|工作站'),
    'carrier': ('Carrier', '载具', r'载具|托盘'),
    'slot': ('PlacementSlot', '放置位', r'放置位|槽位'),
}
DOWNSTREAM = {'part': ('station', 'carrier', 'slot'), 'station': ('carrier', 'slot'), 'carrier': ('slot',)}
UNCERTAIN = r'可能|也许|大概|好像|不(?:太|很|怎么)?确定|未确定|没(?:有)?确定|还没定|待定|还没选|不清楚|不知道|是否|是不是|还是|或|吗[？?]?$|[？?]'
NEGATIVE = r'不要|不用|不选|不再|别用|不是|取消|撤销|先不|暂不|别放|不放'


def catalog_from_graph(graph):
    return [{'kind': kind, 'id': str(entity),
             'label': str(graph.value(entity, RDFS.label) or local_name(entity)),
             'aliases': sorted(str(value) for value in graph.objects(entity, S.alias))}
            for kind, _, _ in ENTITY_FIELDS.values()
            for entity in sorted(set(graph.subjects(RDF.type, S[kind])), key=str)]


def _normalize(text):
    return ''.join(text.split())


def _appears(name, phrase):
    if not name:
        return False
    name = _normalize(name)
    if not name:
        return False
    # Chinese labels can be adjacent (载具A槽位A); ASCII identifiers need word bounds.
    left = r'(?<![A-Za-z0-9_])' if re.fullmatch(r'[A-Za-z0-9_]', name[0]) else ''
    right = r'(?![A-Za-z0-9_])' if re.fullmatch(r'[A-Za-z0-9_]', name[-1]) else ''
    return bool(re.search(left + re.escape(name) + right, phrase))


def grounded_mentions(task, messages, catalog):
    """Return four selected identities and clarification issues, with no writes."""
    selected = dict.fromkeys(ENTITY_FIELDS)
    pending = {}
    for message in messages:
        if message['role'] != 'user':
            continue
        for phrase in re.split(r'(?<=[，,。；;！？!?\n])', _normalize(message['content'])):
            if not phrase:
                continue
            events = {}
            for field, (kind, label, words) in ENTITY_FIELDS.items():
                entries = [item for item in catalog if item['kind'] == kind]
                # An exact full IRI choice disambiguates labels shared by other objects.
                exact = [item for item in entries if _appears(item['id'], phrase)]
                found = exact or [item for item in entries if any(
                    _appears(name, phrase) for name in (item['label'], local_name(item['id']), *item['aliases']))]
                proposed = task[field]
                unknown = proposed if proposed and _appears(proposed, phrase) else None
                if not found and not unknown and not re.search(words, phrase):
                    continue
                candidates = [{'id': item['id'], 'label': item['label']} for item in (found or entries)]
                issue = None
                if re.search(UNCERTAIN + '|' + NEGATIVE, phrase):
                    issue = {'code': 'uncertain_entity', 'field': field,
                             'message': f'{label}尚未明确，请指定要使用的{label}。', 'candidates': candidates}
                elif len(found) > 1:
                    issue = {'code': 'ambiguous_entity', 'field': field,
                             'message': f'存在多个{label}候选，请明确选择一个。', 'candidates': candidates}
                identity = found[0]['id'] if len(found) == 1 else unknown
                events[field] = (None, issue) if issue else (identity, None)
            if not events:
                intent = re.search(r'任务|生成|创建|搬运|抓移放', phrase)
                revoked = re.search(NEGATIVE, phrase) and re.search(r'之前|上述|这些|旧|选择|任务|生成|创建', phrase)
                if re.search(UNCERTAIN, phrase) or revoked:
                    selected = dict.fromkeys(ENTITY_FIELDS)
                    pending.clear()
                    field = 'operation' if intent else 'task'
                    pending[field] = {'code': 'uncertain_' + field, 'field': field,
                                      'message': '任务意图或选择尚未明确，请重新指定任务。', 'candidates': []}
                elif intent:
                    pending.pop('operation', None)
            for field, (identity, issue) in events.items():
                if selected[field] is not None and selected[field] != identity:
                    for dependent in DOWNSTREAM.get(field, ()):
                        selected[dependent] = None
                        pending.pop(dependent, None)
                selected[field] = identity
                if issue:
                    pending[field] = issue
                else:
                    pending.pop(field, None)
                    if identity:
                        pending.pop('task', None)
    return selected, list(pending.values())
