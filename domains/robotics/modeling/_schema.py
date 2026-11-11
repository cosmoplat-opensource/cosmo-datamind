"""Selected schema and ABox assertion checks, adapted from Yang — DR-070."""
from copy import deepcopy
import json
import re
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, ValidationError


ID = {'type': 'string', 'pattern': r'^[A-Za-z][A-Za-z0-9_]*$', 'maxLength': 160}
TEXT = {'type': 'string', 'minLength': 1, 'maxLength': 2000}
REFS = {'type': 'array', 'items': ID, 'uniqueItems': True, 'minItems': 1, 'maxItems': 256}
INVALID_XML_TEXT = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]')
DATATYPES = {'xsd:string': str, 'xsd:integer': int, 'xsd:boolean': bool}


def _object(fields):
    return {'type': 'object', 'properties': fields, 'required': list(fields), 'additionalProperties': False}


def _text(value, label):
    if not isinstance(value, str) or not value.strip() or INVALID_XML_TEXT.search(value):
        raise ValueError(f'{label} 须为非空且可写入 XML 的文本。')


def validate_namespace(namespace):
    if not isinstance(namespace, str):
        raise ValueError('TBox 缺少 namespace。')
    parsed = urlsplit(namespace)
    valid = bool(re.fullmatch(r'urn:[A-Za-z0-9][A-Za-z0-9:._-]*[#/]', namespace)) or (
        parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username and
        not parsed.password and not parsed.query and not parsed.fragment and namespace.endswith(('#', '/')))
    if not valid or any(c.isspace() for c in namespace) or INVALID_XML_TEXT.search(namespace):
        raise ValueError('TBox namespace 须为以 # 或 / 结尾的绝对 HTTP(S) 或 URN。')


def load_tbox(data):
    """Only result selects terms; terminology and historical evidence never add terms."""
    if not isinstance(data, bytes) or not 0 < len(data) <= 10*1024*1024:
        raise ValueError('TBox JSON 须非空且不超过 10 MiB。')
    try:
        document = json.loads(data.decode('utf-8-sig'))
    except (ValueError, UnicodeDecodeError):
        raise ValueError('TBox 须为 UTF-8 编码的合法 JSON。') from None
    if not isinstance(document, dict) or not isinstance(document.get('result'), dict):
        raise ValueError('TBox JSON 缺少 result 骨架对象。')
    namespace = document.get('namespace')
    validate_namespace(namespace)
    tbox = {'namespace': namespace}
    allowed_fields = {
        'classes': {'id', 'label', 'definition', 'parent_ids', 'source_refs', 'cq_refs'},
        'object_properties': {'id', 'label', 'definition', 'domain_ids', 'range_ids', 'source_refs', 'cq_refs'},
        'data_properties': {'id', 'label', 'definition', 'domain_ids', 'class_ids', 'datatype', 'source_refs', 'cq_refs'},
    }
    identifiers = set()
    for key, allowed in allowed_fields.items():
        rows = document['result'].get(key)
        if not isinstance(rows, list) or len(rows) > 1000 or key == 'classes' and not rows:
            raise ValueError(f'TBox result.{key} 须为列表，类别至少一项，每组最多 1000 项。')
        tbox[key] = deepcopy(rows)
        for row in rows:
            if not isinstance(row, dict) or not set(row) <= allowed:
                raise ValueError(f'TBox {key} 包含不支持的字段或约束。')
            identifier = row.get('id')
            if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,95}', identifier):
                raise ValueError('TBox 标识须为 1 至 96 位英文字母、数字或下划线，且以字母开头。')
            if identifier in identifiers:
                raise ValueError('TBox 的类别、关系或属性标识重复。')
            identifiers.add(identifier)
            _text(row.get('label'), f'{identifier} 名称')
            _text(row.get('definition'), f'{identifier} 定义')
    classes = {row['id']: row for row in tbox['classes']}

    def references(row, key, allowed):
        values = row.get(key)
        if (not isinstance(values, list) or any(not isinstance(value, str) for value in values)
                or len(values) != len(set(values)) or not set(values) <= allowed):
            raise ValueError(f'TBox {row["id"]}.{key} 含重复或未选入的类别。')

    for row in tbox['classes']:
        references(row, 'parent_ids', classes.keys())
    visiting, visited = set(), set()

    def visit(identifier):
        if identifier in visiting:
            raise ValueError('TBox 类别层次存在循环。')
        if identifier in visited:
            return
        visiting.add(identifier)
        for parent in classes[identifier]['parent_ids']:
            visit(parent)
        visiting.remove(identifier)
        visited.add(identifier)

    for identifier in classes:
        visit(identifier)
    for key in ('object_properties', 'data_properties'):
        for row in tbox[key]:
            references(row, 'domain_ids', set(classes) | {'owl:Thing'})
            if key == 'object_properties':
                references(row, 'range_ids', set(classes) | {'owl:Thing'})
                if not row['range_ids']:
                    raise ValueError(f'TBox {row["id"]} 缺少关系值域。')
            else:
                if not isinstance(row.get('datatype'), str) or row['datatype'] not in DATATYPES:
                    raise ValueError(f'TBox {row["id"]} 数据类型仅支持 xsd:string、xsd:integer、xsd:boolean。')
                if 'class_ids' in row:
                    references(row, 'class_ids', classes.keys())
    return tbox


def matches_domain(tbox, class_ids, endpoints):
    """Match an instance's types, including parents, to a domain/range union."""
    if not endpoints or 'owl:Thing' in endpoints:
        return True
    classes = {row['id']: row for row in tbox['classes']}
    pending, visited = list(class_ids), set()
    while pending:
        identifier = pending.pop()
        if identifier in endpoints:
            return True
        if identifier not in visited:
            visited.add(identifier)
            pending.extend(classes[identifier]['parent_ids'])
    return False


def response_schema(tbox):
    schema = _object({
        'reply': {**TEXT, 'maxLength': 6000},
        'individuals': {'type': 'array', 'maxItems': 20000, 'items': _object({
            'id': ID, 'label': TEXT,
            'class_ids': {'type': 'array', 'items': {'enum': [row['id'] for row in tbox['classes']]},
                          'minItems': 1, 'uniqueItems': True}, 'source_refs': REFS})},
        'object_assertions': {'type': 'array', 'maxItems': 100000, 'items': _object({
            'subject': ID, 'property': ID, 'object': ID, 'source_refs': REFS})},
        'data_assertions': {'type': 'array', 'maxItems': 100000, 'items': _object({
            'subject': ID, 'property': ID, 'value': {'type': ['string', 'integer', 'boolean']},
            'source_refs': REFS})},
        'clarifications': {'type': 'array', 'items': TEXT, 'uniqueItems': True},
    })
    for key, terms in [('object_assertions', 'object_properties'), ('data_assertions', 'data_properties')]:
        if tbox[terms]:
            schema['properties'][key]['items']['properties']['property'] = {'enum': [row['id'] for row in tbox[terms]]}
        else:
            schema['properties'][key]['maxItems'] = 0
    if tbox['data_properties']:
        schema['properties']['data_assertions']['items']['oneOf'] = [
            {'properties': {'property': {'enum': identifiers}, 'value': {'type': kind}}}
            for datatype, kind in [('xsd:string', 'string'), ('xsd:integer', 'integer'), ('xsd:boolean', 'boolean')]
            if (identifiers := [row['id'] for row in tbox['data_properties'] if row['datatype'] == datatype])]
    return schema


def validate_result(result, tbox, evidence, message_refs, *, check_associations=True):
    try:
        Draft202012Validator(response_schema(tbox)).validate(result)
    except ValidationError as exc:
        path = '/'.join(map(str, exc.absolute_path)) or 'response'
        raise ValueError(f'ABox 结果不符合契约：{path}（{exc.validator}）。') from None
    result = deepcopy(result)
    _text(result['reply'], '对话回复')
    for clarification in result['clarifications']:
        _text(clarification, '待明确项')
    if not result['individuals'] and not result['clarifications']:
        raise ValueError('未抽取实例时必须说明缺失依据或待明确项。')
    allowed_refs = {row['id'] for row in evidence} | set(message_refs)
    individuals = {row['id']: row for row in result['individuals']}
    if len(individuals) != len(result['individuals']):
        raise ValueError('实例标识重复。')
    for row in result['individuals']:
        _text(row['label'], '实例名称')

    def matches(identifier, endpoints):
        return matches_domain(tbox, individuals[identifier]['class_ids'], endpoints)

    for group in ('individuals', 'object_assertions', 'data_assertions'):
        for row in result[group]:
            if not set(row['source_refs']) <= allowed_refs:
                raise ValueError('实例或断言引用了不存在的来源。')
            for field in ('id', 'subject', 'object'):
                if field in row and not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', row[field]):
                    raise ValueError('实例标识含非法字符。')
    for key, property_key in [('object_assertions', 'object_properties'), ('data_assertions', 'data_properties')]:
        terms = {row['id']: row for row in tbox[property_key]}
        seen = set()
        for row in result[key]:
            if row['subject'] not in individuals or key == 'object_assertions' and row['object'] not in individuals:
                raise ValueError('断言指向本轮不存在的实例。')
            term = terms[row['property']]
            domains = term['domain_ids']
            if key == 'data_assertions' and check_associations and not domains:
                domains = term.get('class_ids', [])
            if not matches(row['subject'], domains):
                raise ValueError(f'{row["property"]} 主体类型不符合 TBox 定义域或关联类别。')
            if key == 'object_assertions':
                if not matches(row['object'], term['range_ids']):
                    raise ValueError(f'{row["property"]} 客体类型不符合 TBox 值域或关系方向。')
                identity = (row['subject'], row['property'], row['object'])
            else:
                if type(row['value']) is not DATATYPES[term['datatype']]:
                    raise ValueError(f'{row["property"]} 值类型必须与 TBox 的 {term["datatype"]} 完全一致。')
                if isinstance(row['value'], str) and INVALID_XML_TEXT.search(row['value']):
                    raise ValueError('数据属性值含无法写入 XML 的字符。')
                identity = (row['subject'], row['property'], type(row['value']), row['value'])
            if identity in seen:
                raise ValueError('同一实例断言重复，请合并其来源。')
            seen.add(identity)
    return result
