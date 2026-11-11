"""Bundled controlled vocabulary, adapted from Yang — DR-070."""
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit


VOCABULARY_PATH = Path(__file__).resolve().parent/'data'/'ur_skill_terminology.json'
INVALID_TEXT = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]')


def load_vocabulary():
    """Return independent checked terms and the bundled file fingerprint."""
    path, include_properties = VOCABULARY_PATH, True
    try:
        raw = Path(path).read_bytes()
        document = json.loads(raw)
    except (OSError, ValueError, UnicodeError):
        raise ValueError('术语词表无法读取或不是有效 JSON，请检查 ur_skill_terminology.json。') from None
    if not isinstance(document, dict) or not isinstance(document.get('terms'), list):
        raise ValueError('术语词表必须包含 terms 数组。')
    metadata = document.get('metadata', {})
    if not isinstance(metadata, dict):
        raise ValueError('术语词表 metadata 必须是 JSON 对象。')
    if 'version' in metadata:
        version = metadata['version']
        if not isinstance(version, str) or not version.strip() or len(version) > 100 or INVALID_TEXT.search(version):
            raise ValueError('术语词表 version 须为 1 至 100 个有效字符。')
    if 'term_changes' in metadata:
        if not isinstance(metadata['term_changes'], dict):
            raise ValueError('术语词表 term_changes 必须是 JSON 对象。')
        try:
            json.dumps(metadata['term_changes'], ensure_ascii=False).encode('utf-8')
        except UnicodeError:
            raise ValueError('术语词表 term_changes 包含无效 Unicode 字符。') from None
    groups = {'class': [], 'object_property': [], 'data_property': []}
    identifiers, iris = set(), set()
    labels = {kind: set() for kind in groups}
    fields = {'label': ('zh_name', 100), 'en_label': ('en_name', 100),
              'definition': ('editorial_definition_zh', 800), 'note': ('alignment_note', 1000)}
    for term in document['terms']:
        if not isinstance(term, dict) or not isinstance(term.get('kind'), str):
            raise ValueError('术语词表 terms 中的条目必须有实体种类 kind。')
        kind = term['kind']
        if kind not in groups or not include_properties and kind != 'class':
            continue
        identifier = term.get('id')
        if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,95}', identifier):
            raise ValueError('术语词表的类别标识必须是长度不超过 96 的英文标识。')
        row = {'id': identifier}
        for key, (source, limit) in fields.items():
            value = term.get(source)
            if (not isinstance(value, str) or len(value) > limit or INVALID_TEXT.search(value)
                    or key != 'note' and not value.strip()):
                raise ValueError(f'术语词表类别 {identifier} 的 {source} 缺失或无效。')
            row[key] = value
        row['scope'] = term.get('alignment_role')
        if row['scope'] not in ('schema', 'metadata', 'scope_review', 'conditional'):
            raise ValueError(f'术语词表类别 {identifier} 的 alignment_role 无效。')
        iri = term.get('iri')
        valid_iri = False
        if isinstance(iri, str) and iri and not any(c.isspace() for c in iri) and not INVALID_TEXT.search(iri):
            try:
                parsed = urlsplit(iri)
                valid_iri = bool(parsed.scheme in ('http', 'https') and parsed.hostname
                                 and parsed.port != 0 and not parsed.username and not parsed.password
                                 or re.fullmatch(r'urn:[A-Za-z0-9][A-Za-z0-9-]*:.+', iri))
            except ValueError:
                pass
        if not valid_iri:
            raise ValueError(f'术语词表类别 {identifier} 的完整 IRI 无效。')
        if identifier in identifiers or row['label'] in labels[kind] or iri in iris:
            raise ValueError('术语词表类别的标识、中文名称或完整 IRI 重复。')
        identifiers.add(identifier)
        labels[kind].add(row['label'])
        iris.add(iri)
        row['iri'] = iri
        groups[kind].append(row)
    classes = groups['class']
    if not classes:
        raise ValueError('术语词表未包含可用的类别术语。')
    vocabulary = {'source': 'robotics/modeling/data/ur_skill_terminology.json',
                  'sha256': hashlib.sha256(raw).hexdigest(), 'classes': classes}
    for key in ('version', 'term_changes'):
        if key in metadata:
            vocabulary[key] = metadata[key]
    if include_properties:
        class_iris = {row['iri']: row['id'] for row in classes}
        class_iris['http://www.w3.org/2002/07/owl#Thing'] = 'owl:Thing'
        source = {row['id']: row for row in document['terms']
                  if row['kind'] in ('object_property', 'data_property')}
        for kind, key in [('object_property', 'object_properties'), ('data_property', 'data_properties')]:
            vocabulary[key] = groups[kind]
            for row in groups[kind]:
                term = source[row['id']]
                row['domain_ids'] = _class_expression(term.get('domains'), class_iris, row['id'])
                if kind == 'object_property':
                    row['range_ids'] = _class_expression(term.get('ranges'), class_iris, row['id'])
                    if not row['range_ids']:
                        raise ValueError(f'术语词表关系 {row["id"]} 缺少类别值域。')
                else:
                    ranges = term.get('ranges')
                    xsd = 'http://www.w3.org/2001/XMLSchema#'
                    if not isinstance(ranges, list) or len(ranges) != 1 or ranges[0] not in (
                            xsd+'string', xsd+'boolean', xsd+'integer'):
                        raise ValueError(f'术语词表数据属性 {row["id"]} 的数据类型无效；当前支持 xsd:string、xsd:boolean、xsd:integer。')
                    row['datatype'] = 'xsd:'+ranges[0][len(xsd):]
    return vocabulary


def _class_expression(expressions, class_iris, identifier):
    if not isinstance(expressions, list) or len(expressions) > 1:
        raise ValueError(f'术语词表 {identifier} 的定义域或值域须为单一声明，不能把多个交集断言当作并集。')
    if not expressions:
        return []
    expression = expressions[0]
    members = expression.get('unionOf') if isinstance(expression, dict) and set(expression) == {'unionOf'} else [expression]
    if (not isinstance(members, list) or not members or any(not isinstance(member, str) or member not in class_iris for member in members)
            or len(set(members)) != len(members)):
        raise ValueError(f'术语词表 {identifier} 的类别定义域或值域无效。')
    return [class_iris[member] for member in members]
