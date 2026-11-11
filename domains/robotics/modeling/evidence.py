"""Role-bound XML evidence, adapted from Yang documents.py — DR-070."""
from collections import Counter
from copy import deepcopy
import hashlib
import json
import xml.etree.ElementTree as ET

from ..source_admission import MAX_DEPTH, MAX_NODES, validate_source
from .common import ModelingError

ROLES = ('behavior_tree', 'node_models')
MAX_FILE_BYTES = 10 * 1024 * 1024


class _EvidenceBuilder(ET.TreeBuilder):
    """Count retained comments/PIs as well as elements, before constructing facts."""
    def __init__(self):
        super().__init__(insert_comments=True, insert_pis=True)
        self.nodes = 0
        self.depth = 0

    def _count(self):
        self.nodes += 1
        if self.nodes > MAX_NODES or self.depth > MAX_DEPTH:
            raise ModelingError('invalid_source', 'XML 证据超过 20000 个节点或 100 层限制。')

    def start(self, tag, attrs):
        self.depth += 1
        self._count()
        return super().start(tag, attrs)

    def end(self, tag):
        self.depth -= 1
        return super().end(tag)

    def comment(self, text):
        self._count()
        return super().comment(text)

    def pi(self, target, text=None):
        self._count()
        return super().pi(target, text)


def ingest_sources(sources):
    """Fixed role order; dedup facts but retain every occurrence and original digest."""
    if not isinstance(sources, dict) or set(sources) != set(ROLES):
        raise ModelingError('invalid_source', '需要行为树与节点模型两份 XML。')
    documents, evidence, by_fact = [], [], {}
    for index, role in enumerate(ROLES, 1):
        data = sources[role]
        if not isinstance(data, bytes) or not 0 < len(data) <= MAX_FILE_BYTES:
            raise ModelingError('invalid_source', 'XML 须非空且不超过 10 MiB。')
        validate_source(data, role=role)
        parser = ET.XMLParser(target=_EvidenceBuilder())
        root = ET.fromstring(data, parser=parser)
        document = {'id': f'D{index:03d}', 'role': role, 'size': len(data),
                    'sha256': hashlib.sha256(data).hexdigest()}
        documents.append(document)
        for kind, facts, path in _facts(root):
            key = json.dumps([kind, facts], ensure_ascii=False, sort_keys=True)
            if key not in by_fact:
                row = {'id': f'E{len(evidence)+1:05d}', 'kind': kind, 'facts': facts, 'locations': []}
                evidence.append(row)
                by_fact[key] = row
            by_fact[key]['locations'].append({'document_id': document['id'], 'path': path})
    return {'documents': documents, 'evidence': evidence}


def source_digests(bundle):
    """Validate stored occurrence structure; bytes are retained by the host source store."""
    if not isinstance(bundle, dict) or not isinstance(bundle.get('documents'), list):
        raise ModelingError('invalid_source', 'XML 证据缺少文档身份。')
    documents = bundle['documents']
    if (len(documents) != 2 or {d.get('role') for d in documents} != set(ROLES)
            or len({d.get('id') for d in documents}) != 2):
        raise ModelingError('invalid_source', 'XML 证据来源角色或标识无效。')
    for doc in documents:
        sha = doc.get('sha256')
        if not isinstance(sha, str) or len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha):
            raise ModelingError('invalid_source', 'XML 来源摘要无效。')
    summary = model_summary(bundle['evidence'])
    if {d['document_id'] for d in summary['trees']} != {d['id'] for d in documents}:
        raise ModelingError('invalid_source', 'XML 证据与文档身份不一致。')
    return {d['role']: d['sha256'] for d in documents}


def _tag(node):
    if node.tag is ET.Comment:
        return 'comment()'
    if node.tag is ET.ProcessingInstruction:
        return 'processing-instruction()'
    return node.tag


def _local(tag):
    return tag.rsplit('}', 1)[-1]


def _path_tag(tag):
    return 'Q'+tag if tag.startswith('{') else tag


def _facts(root):
    def visit(node, path, parent_tag='', in_tree=False):
        tag = _tag(node)
        local = _local(tag)
        kind = ('bt_tree' if local == 'BehaviorTree' else
                'bt_contract' if parent_tag == 'TreeNodesModel' else
                'bt_call' if in_tree else 'xml')
        facts = {'tag': tag, 'attributes': dict(node.attrib),
                 'children': [_tag(child) for child in node]}
        for field in ('text', 'tail'):
            value = getattr(node, field)
            if value and value.strip():
                facts[field] = value
        yield kind, facts, path
        counters = Counter()
        for child in node:
            child_tag = _tag(child)
            counters[child_tag] += 1
            child_path = path+'/'+_path_tag(child_tag)+f'[{counters[child_tag]}]'
            yield from visit(child, child_path, local, in_tree or local == 'BehaviorTree')
    yield from visit(root, '/'+_path_tag(_tag(root))+'[1]')



def model_summary(evidence):
    """Replace long source paths with lossless, per-document node occurrence trees."""
    by_id = {row['id']: row for row in evidence}
    locations, documents = {}, {}
    if len(by_id) != len(evidence):
        raise ValueError('XML 证据标识重复，无法整理模型上下文。')
    for row in evidence:
        if not row['locations']:
            raise ValueError('XML 证据缺少来源位置，无法整理模型上下文。')
        for location in row['locations']:
            key = (location['document_id'], location['path'])
            if key in locations:
                raise ValueError('XML 来源位置重复，无法整理模型上下文。')
            locations[key] = row['id']
            documents[location['document_id']] = None
    visited, trees = set(), []
    for document_id in sorted(documents):
        roots = [(path, eid) for (doc, path), eid in locations.items()
                 if doc == document_id and path == '/'+_path_tag(by_id[eid]['facts']['tag'])+'[1]']
        if len(roots) != 1:
            raise ValueError('每份 XML 证据必须有唯一根节点，无法整理模型上下文。')
        nodes = []
        def visit(path, eid, parent, document_id=document_id, nodes=nodes):
            key = (document_id, path)
            if key in visited:
                raise ValueError('XML 来源结构重复，无法整理模型上下文。')
            visited.add(key)
            index = len(nodes)
            nodes.append([eid, parent])
            counts = Counter()
            for tag in by_id[eid]['facts']['children']:
                counts[tag] += 1
                child_path = path+'/'+_path_tag(tag)+f'[{counts[tag]}]'
                child = locations.get((document_id, child_path))
                if child is None:
                    raise ValueError('XML 来源存在缺失的子节点，无法整理模型上下文。')
                visit(child_path, child, index)
        visit(*roots[0], -1)
        trees.append({'document_id': document_id, 'nodes': nodes})
    if len(visited) != len(locations):
        raise ValueError('XML 来源有未覆盖的位置，无法整理模型上下文。')
    return {'facts': [{key: deepcopy(row[key]) for key in ('id', 'kind', 'facts')} for row in evidence],
            'trees': trees}
