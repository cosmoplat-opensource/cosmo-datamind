"""TBox proposal contracts and local semantics, adapted from Yang — DR-070."""
from copy import deepcopy
import re

from jsonschema import Draft202012Validator, ValidationError


ID = {'type': 'string', 'pattern': r'^[A-Za-z][A-Za-z0-9_]*$', 'maxLength': 96}
TEXT = {'type': 'string', 'minLength': 1, 'maxLength': 1000}
REFS = {'type': 'array', 'items': ID, 'uniqueItems': True, 'minItems': 1, 'maxItems': 256}
INVALID_XML_TEXT = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]')


def _object(fields):
    return {'type': 'object', 'properties': fields, 'required': list(fields), 'additionalProperties': False}


RESPONSE_SCHEMA = _object({
    'reply': {**TEXT, 'maxLength': 6000},
    'competency_questions': {'type': 'array', 'maxItems': 50, 'items': _object({
        'id': ID, 'question': TEXT, 'source_refs': REFS})},
    'classes': {'type': 'array', 'maxItems': 100, 'items': _object({
        'id': ID, 'label': {**TEXT, 'maxLength': 100}, 'definition': {**TEXT, 'maxLength': 800},
        'parent_ids': {'type': 'array', 'items': ID, 'uniqueItems': True, 'maxItems': 100},
        'source_refs': REFS, 'cq_refs': {**REFS, 'maxItems': 50}})},
    'clarifications': {'type': 'array', 'items': TEXT, 'uniqueItems': True},
})


ENDPOINTS = {'type': 'array', 'items': {'anyOf': [ID, {'const': 'owl:Thing'}]},
             'uniqueItems': True, 'maxItems': 100}
PROPERTY_FIELDS = {'id': ID, 'label': {**TEXT, 'maxLength': 100},
                   'definition': {**TEXT, 'maxLength': 800}, 'domain_ids': ENDPOINTS,
                   'source_refs': REFS, 'cq_refs': {**REFS, 'maxItems': 50}}
PROPERTY_RESPONSE_SCHEMA = _object({
    'reply': {**TEXT, 'maxLength': 6000},
    'object_properties': {'type': 'array', 'maxItems': 100, 'items': _object({
        **PROPERTY_FIELDS, 'range_ids': {**ENDPOINTS, 'minItems': 1}})},
    'data_properties': {'type': 'array', 'maxItems': 100, 'items': _object({
        **PROPERTY_FIELDS, 'class_ids': {**REFS, 'maxItems': 100},
        'datatype': {'enum': ['xsd:string', 'xsd:boolean', 'xsd:integer']}})},
    'clarifications': {'type': 'array', 'items': TEXT, 'uniqueItems': True},
})


def response_schema(terminology, stage='classes', classes=None):
    if stage == 'properties':
        schema = deepcopy(PROPERTY_RESPONSE_SCHEMA)
        class_ids = [row['id'] for row in classes or []]
        endpoints = class_ids+['owl:Thing']
        for key in ('object_properties', 'data_properties'):
            field = schema['properties'][key]
            candidates = [row for row in terminology.get(key, [])
                          if row['scope'] != 'scope_review'
                          and set(row['domain_ids']) <= set(endpoints)
                          and (key == 'data_properties' or set(row['range_ids']) <= set(endpoints))]
            if not candidates or key == 'data_properties' and not class_ids:
                field['maxItems'] = 0
                continue
            fields = field['items']['properties']
            fields['id'] = {**fields['id'], 'enum': [row['id'] for row in candidates]}
            fields['domain_ids'] = {**fields['domain_ids'], 'items': {'enum': endpoints}}
            if key == 'object_properties':
                fields['range_ids'] = {**fields['range_ids'], 'items': {'enum': endpoints}}
            else:
                fields['domain_ids'] = {**fields['domain_ids'], 'items': {'enum': class_ids}}
                fields['class_ids'] = {**fields['class_ids'], 'items': {'enum': class_ids}}
                fields['datatype'] = {'enum': sorted({row['datatype'] for row in candidates})}
        return schema
    if stage != 'classes':
        raise ValueError('未知的本体设计阶段。')
    schema = deepcopy(RESPONSE_SCHEMA)
    identifiers = [row['id'] for row in terminology['classes'] if row['scope'] != 'scope_review']
    if not identifiers:
        schema['properties']['classes']['maxItems'] = 0
        return schema
    fields = schema['properties']['classes']['items']['properties']
    fields['id'] = {**fields['id'], 'enum': identifiers}
    fields['parent_ids']['items'] = {**fields['parent_ids']['items'], 'enum': identifiers}
    return schema


def validate_result(result, source_refs, message_refs, terminology=None):
    """Return a checked copy, adding explicit gaps for unmapped capability questions."""
    try:
        Draft202012Validator(RESPONSE_SCHEMA).validate(result)
    except ValidationError as exc:
        path = '/'.join(map(str, exc.absolute_path)) or 'response'
        raise ValueError(f'模型结果不符合类别契约：{path}（{exc.validator}）。') from None
    result = deepcopy(result)
    texts = [result['reply'], *result['clarifications'],
             *[row['question'] for row in result['competency_questions']],
             *[row[field] for row in result['classes'] for field in ('label', 'definition')]]
    if any(INVALID_XML_TEXT.search(text) for text in texts):
        raise ValueError('模型文本包含无效控制字符或 Unicode 字符，无法安全保存和导出。')
    if not result['reply'].strip() or any(not s.strip() for s in result['clarifications']):
        raise ValueError('对话回复和澄清问题不能仅包含空白。')
    if not result['classes'] and not result['clarifications']:
        raise ValueError('未生成类别时必须明确说明需要补充的信息。')
    questions = {row['id']: row for row in result['competency_questions']}
    classes = {row['id']: row for row in result['classes']}
    if any(not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', identifier) for identifier in (*questions, *classes)):
        raise ValueError('类别和能力问题须使用英文标识，只能包含字母、数字和下划线。')
    if len(questions) != len(result['competency_questions']) or len(classes) != len(result['classes']):
        raise ValueError('类别标识或能力问题标识重复。')
    if terminology is not None:
        terms = {row['id']: row for row in terminology['classes']}
        names = {row['label']: row['id'] for row in terminology['classes']}
        for row in classes.values():
            term = terms.get(row['id'])
            if term is None:
                raise ValueError(f'类别 {row["id"]} 未收录于术语词表；请先在待明确项中提出，确认并补充词表后再生成。')
            if term['scope'] == 'scope_review':
                raise ValueError(f'词表中的 {term["label"]} 属于范围待确认项；确认并更新词表前不能纳入类别快照。')
            if row['label'].strip() in names and names[row['label'].strip()] != row['id']:
                raise ValueError(f'类别 {row["id"]} 的名称与词表中另一个类别冲突；请明确类别身份。')
            if not row['label'].strip():
                raise ValueError('类别名称不能为空。')
            row['label'] = term['label']
    allowed = set(source_refs) | set(message_refs)
    for row in questions.values():
        if not row['question'].strip() or not set(row['source_refs']) <= set(message_refs):
            raise ValueError('能力问题必须引用已有用户消息，且内容不能为空。')
    for row in classes.values():
        if not row['label'].strip() or not row['definition'].strip():
            raise ValueError('类别名称和定义不能为空。')
        if not set(row['source_refs']) <= allowed:
            raise ValueError('类别引用了不存在的来源。')
        if not set(row['parent_ids']) <= classes.keys():
            raise ValueError('类别引用了不存在的父类。')
        if not set(row['cq_refs']) <= questions.keys():
            raise ValueError('类别引用了不存在的能力问题。')
    visiting, visited = set(), set()
    def visit(identifier):
        if identifier in visiting:
            raise ValueError('类别父子层次存在循环。')
        if identifier in visited:
            return
        visiting.add(identifier)
        for parent in classes[identifier]['parent_ids']:
            visit(parent)
        visiting.remove(identifier)
        visited.add(identifier)
    for identifier in classes:
        visit(identifier)
    mapped = {ref for row in classes.values() for ref in row['cq_refs']}
    for identifier in questions:
        if identifier in mapped:
            continue
        gap = f'能力问题 {identifier} 尚未关联类别，请补充依据或明确本轮范围；类别相关性不代表已回答该问题。'
        if gap not in result['clarifications']:
            result['clarifications'].append(gap)
    return result


def validate_properties(result, source_refs, message_refs, terminology, classes, questions):
    """Check properties against the frozen classes and the registered term semantics."""
    try:
        Draft202012Validator(PROPERTY_RESPONSE_SCHEMA).validate(result)
    except ValidationError as exc:
        path = '/'.join(map(str, exc.absolute_path)) or 'response'
        raise ValueError(f'模型结果不符合关系与属性契约：{path}（{exc.validator}）。') from None
    result = deepcopy(result)
    texts = [result['reply'], *result['clarifications'],
             *[row[field] for key in ('object_properties', 'data_properties')
               for row in result[key] for field in ('label', 'definition')]]
    if any(INVALID_XML_TEXT.search(text) or not text.strip() for text in texts):
        raise ValueError('关系与属性文本不能为空或包含无效 Unicode 字符。')
    if not result['object_properties'] and not result['data_properties'] and not result['clarifications']:
        raise ValueError('未生成关系或属性时必须明确说明需要补充的信息。')
    class_ids = {row['id'] for row in classes}
    question_ids = {row['id'] for row in questions}
    endpoints, allowed = class_ids | {'owl:Thing'}, set(source_refs) | set(message_refs)
    identifiers = set(class_ids)
    for key in ('object_properties', 'data_properties'):
        terms = {row['id']: row for row in terminology.get(key, [])}
        names = {row['label']: row['id'] for row in terminology.get(key, [])}
        for row in result[key]:
            identifier = row['id']
            if identifier in identifiers:
                raise ValueError('类别、关系或属性标识重复。')
            identifiers.add(identifier)
            term = terms.get(identifier)
            if term is None:
                raise ValueError(f'关系或属性 {identifier} 未收录于对应种类的术语词表，请先列入待明确项。')
            if term['scope'] == 'scope_review':
                raise ValueError(f'词表中的 {term["label"]} 属于范围待确认项。')
            if row['label'].strip() in names and names[row['label'].strip()] != identifier:
                raise ValueError(f'关系或属性 {identifier} 的名称与同种类词表术语冲突。')
            row['label'] = term['label']
            if not set(row['source_refs']) <= allowed or not set(row['cq_refs']) <= question_ids:
                raise ValueError('关系或属性引用了不存在的来源或能力问题。')
            if not set(row['domain_ids']) <= endpoints:
                raise ValueError('关系或属性的定义域引用了未确认的类别。')
            if set(row['domain_ids']) != set(term['domain_ids']):
                raise ValueError(f'{identifier} 的定义域与词表不一致；不能反向、缩窄并集或补造定义域。')
            if key == 'object_properties':
                if not set(row['range_ids']) <= endpoints:
                    raise ValueError('关系值域引用了未确认的类别。')
                if set(row['range_ids']) != set(term['range_ids']):
                    raise ValueError(f'{identifier} 的值域或关系方向与词表不一致。')
            else:
                if not set(row['class_ids']) <= class_ids:
                    raise ValueError('数据属性关联了未确认的类别。')
                if row['datatype'] != term['datatype']:
                    raise ValueError(f'{identifier} 的数据类型与词表不一致。')
            if term['scope'] == 'conditional':
                clarification = (f'条件采用项 {term["label"]}（{identifier}）：请按词表适用说明审查；'
                                 '预期效果或条件描述不代表已经发生的状态或已满足的事实。')
                if clarification not in result['clarifications']:
                    result['clarifications'].append(clarification)
    return result
