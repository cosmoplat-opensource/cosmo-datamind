"""Deterministic XML rules adapted from Yang; no model or persistence — DR-070."""
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from .evidence import model_summary
from ._schema import matches_domain


MAPPING_FILE = Path(__file__).resolve().parent / 'data' / 'xml_mapping.json'
VARIABLE = re.compile(r'\{([A-Za-z_][A-Za-z0-9_]*)\}\Z')
IGNORED_NODES = {'comment()', 'processing-instruction()', ET.Comment, ET.ProcessingInstruction}
OPTIONAL_ROLES = {
    'classes': {'perception_profile', 'part_type'},
    'object_properties': {'perception_profile'},
    'data_properties': {'declared_default', 'default_present', 'raw_value', 'node_kind', 'source_model_xml',
                        'object_name', 'model_name', 'threshold', 'exposure_time'},
}
PERCEPTION_FIELDS = {'object_name': 'object_name', 'model_name': 'model_name',
                     'threshold': 'threshold', 'exposure_time': 'exposure_time'}


def _identifier(prefix, *parts):
    raw = '_'.join([prefix, *map(str, parts)])
    safe = re.sub(r'[^A-Za-z0-9_]', '_', raw)
    if safe != raw or len(safe) > 140:
        safe = safe[:140]+'_'+hashlib.sha256(raw.encode()).hexdigest()[:12]
    return safe


def _xml_roots(evidence):
    summary = model_summary(evidence)
    facts = {row['id']: row['facts'] for row in summary['facts']}
    roots, sources = [], {}
    for document in summary['trees']:
        nodes = []
        for ref, parent in document['nodes']:
            fact = facts[ref]
            tag = {'comment()': ET.Comment, 'processing-instruction()': ET.ProcessingInstruction}.get(fact['tag'], fact['tag'])
            # DR-070: JSON object ordering is not XML semantics; canonicalize after any restart.
            node = ET.Element(tag, dict(sorted(fact['attributes'].items())))
            node.text = fact.get('text')
            node.tail = fact.get('tail')
            nodes.append(node)
            sources[node] = ref
            if parent < 0:
                roots.append(node)
            else:
                nodes[parent].append(node)
    return roots, sources


class ExtractionError(ValueError):
    def __init__(self, message, source_refs):
        super().__init__(message)
        self.source_refs = list(dict.fromkeys(source_refs))


def _ports(model, source):
    ports = {}
    for port in model:
        if port.tag in IGNORED_NODES:
            continue
        name = port.get('name')
        if port.tag not in ('input_port', 'output_port') or not name or name in ports:
            raise ExtractionError(f'接口 {model.get("ID")} 含不支持、缺名或重复端口。', [source[port]])
        ports[name] = (port.tag.removesuffix('_port'), port)
    return ports


def _model_xml(model):
    snapshot = ET.Element(model.tag, model.attrib)
    snapshot.text = model.text
    snapshot.extend(model)
    return ET.tostring(snapshot, encoding='unicode')


def build_result(tbox, evidence, include_unused=False):
    if type(include_unused) is not bool:
        raise ValueError('include_unused 须为布尔值。')
    mapping_bytes = MAPPING_FILE.read_bytes()
    mapping = json.loads(mapping_bytes.decode('utf-8'))
    if not isinstance(mapping, dict) or set(mapping) != {'classes', 'object_properties', 'data_properties'}:
        raise ValueError('XML 映射须且仅须包含 classes、object_properties、data_properties 三组。')
    for kind, roles in mapping.items():
        if kind not in ('classes', 'object_properties', 'data_properties') or not isinstance(roles, dict):
            raise ValueError('XML 映射只接受类型、关系、属性的角色到标识映射。')
        if any(not isinstance(value, str) for value in roles.values()):
            raise ValueError('XML 映射目标必须是术语标识字符串。')
    selected = {kind: {row['id'] for row in tbox[kind]} for kind in mapping}
    missing = [{'kind': kind, 'role': role, 'term_id': term}
               for kind, roles in mapping.items() for role, term in roles.items()
               if term not in selected[kind] and role not in OPTIONAL_ROLES.get(kind, set())]
    report = {'method': 'xml_rules', 'include_unused': include_unused, 'missing_terms': missing,
              'mapping_sha256': hashlib.sha256(mapping_bytes).hexdigest(),
              'selected_unmapped_terms': {kind: sorted(selected[kind]-set(mapping[kind].values()))
                                          for kind in mapping},
              'omitted_information': [], 'skipped_instances': [], 'skipped_assertions': [],
              'inferred_interfaces': [], 'source_coverage': {}}
    result = {'reply': '', 'individuals': [], 'object_assertions': [], 'data_assertions': [], 'clarifications': []}
    roots, source = _xml_roots(evidence)

    def fail(message, *nodes):
        raise ExtractionError(message, [source[node] for node in nodes])

    trees, model_groups = {}, defaultdict(list)
    for root in roots:
        for tree in root.findall('BehaviorTree'):
            name = tree.get('ID')
            if not name or name in trees:
                fail('BehaviorTree 标识缺失或重复，无法确定调用身份。', tree)
            children = [node for node in tree if node.tag not in IGNORED_NODES]
            if len(children) != 1 or children[0].tag != 'Sequence':
                fail(f'流程 {name} 仅支持单层 Sequence，其他控制结构尚未实现。', tree)
            for node in children[0]:
                if node.tag not in IGNORED_NODES and len(node):
                    fail(f'流程 {name} 不支持嵌套控制或带子节点的调用。', node)
            trees[name] = tree
        priority = int(not root.findall('BehaviorTree'))
        for model in root.findall('TreeNodesModel/*'):
            if model.tag in IGNORED_NODES:
                continue
            if model.tag not in ('Action', 'Condition', 'SubTree') or not model.get('ID'):
                fail('仅支持具名 Action、Condition、SubTree 接口。', model)
            model_groups[model.get('ID')].append((priority, model))
    if not trees:
        fail('XML 中未找到 BehaviorTree 流程，无法构建实例图谱。', *roots)
    calls = [(name, index, node) for name, tree in trees.items()
             for index, node in enumerate((n for n in tree.find('Sequence') if n.tag not in IGNORED_NODES), 1)]
    callers = defaultdict(list)
    for _tree_id, _index, node in calls:
        if node.tag == 'SubTree':
            target = node.get('ID')
            if target not in trees:
                fail(f'SubTree 引用了不存在的流程 {target}。', node)
            callers[target].append(node)
    models, ports = {}, {}
    for name, declarations in model_groups.items():
        declarations.sort(key=lambda item: item[0], reverse=True)
        model = declarations[0][1]
        contract = _ports(model, source)
        for _, other in declarations[1:]:
            alternative = _ports(other, source)
            if model.tag != other.tag or set(contract) != set(alternative):
                fail(f'节点 {name} 的接口声明互相冲突。', model, other)
            for key, (direction, port) in contract.items():
                other_direction, other_port = alternative[key]
                if (direction != other_direction or port.get('type') and other_port.get('type')
                        and port.get('type') != other_port.get('type')):
                    fail(f'节点 {name}.{key} 的方向或类型声明冲突。', port, other_port)
        models[name], ports[name] = model, contract
    active = {node.tag for _, _, node in calls if node.tag != 'SubTree'}
    builtin_nodes = [node for _, _, node in calls if node.tag == 'SetBlackboard' and node.tag not in models]
    directions = {}
    atomic_names = active | ({name for name in models if models[name].tag != 'SubTree'} if include_unused else set())
    for name in sorted(atomic_names):
        if name == 'SetBlackboard' and name not in models:
            directions[name] = {'value': 'input', 'output_key': 'input'}
        elif name not in models:
            fail(f'节点 {name} 缺少接口声明，不能猜测参数方向。', *[n for _, _, n in calls if n.tag == name])
        else:
            if models[name].tag == 'SubTree':
                fail(f'节点 {name} 的调用种类与 SubTree 接口不一致。', models[name])
            directions[name] = {key: value[0] for key, value in ports[name].items()}
    visiting = set()

    def infer_interface(tree_id):
        if tree_id in directions:
            return directions[tree_id]
        if tree_id in visiting:
            fail('SubTree 调用存在循环，无法静态确定接口。', *callers[tree_id])
        visiting.add(tree_id)
        events = defaultdict(list)
        for name, index, node in calls:
            if name != tree_id:
                continue
            tag = node.get('ID') if node.tag == 'SubTree' else node.tag
            current = infer_interface(tag) if node.tag == 'SubTree' else directions[tag]
            for key, raw in node.attrib.items():
                if key in ('ID', 'name'):
                    continue
                if key not in current:
                    fail(f'{tree_id}/{index}.{key} 没有已声明或可推导的参数方向。', node)
                match = VARIABLE.fullmatch(raw)
                if match:
                    events[match.group(1)].append((index, current[key], node))
            if tag == 'SetBlackboard':
                key = node.get('output_key')
                if not key or VARIABLE.fullmatch(key):
                    fail('SetBlackboard 的动态或空目标键暂不支持静态抽取。', node)
                events[key].append((index, 'output', node))
        public = set().union(*(set(node.attrib)-{'ID', 'name'} for node in callers.get(tree_id, [])))
        declared = ports.get(tree_id, {}) if tree_id in models and models[tree_id].tag == 'SubTree' else {}
        if declared and not public <= set(declared):
            fail(f'子树 {tree_id} 的调用包含未声明的公开参数。', *callers[tree_id])
        names = list(declared) if declared else sorted(public)
        interface = {}
        for key in names:
            writes = [index for index, direction, _ in events[key] if direction == 'output']
            reads = [index for index, direction, _ in events[key] if direction == 'input']
            read_first = bool(reads and (not writes or min(reads) <= min(writes)))
            if read_first and writes:
                fail(f'子树 {tree_id}.{key} 需要双向读写接口，当前规则尚不支持。', trees[tree_id])
            inferred = 'input' if read_first else 'output' if writes else None
            if declared:
                direction = declared[key][0]
                if inferred and direction != inferred:
                    fail(f'子树 {tree_id}.{key} 的声明方向与内部读写冲突。', declared[key][1], trees[tree_id])
            elif inferred:
                direction = inferred
            else:
                fail(f'子树 {tree_id}.{key} 缺少内部读写依据，不能猜测方向。', trees[tree_id])
            interface[key] = direction
        directions[tree_id] = interface
        visiting.remove(tree_id)
        if tree_id in callers:
            report['inferred_interfaces'].append({'tree_id': tree_id, 'declared': bool(declared),
                                                  'parameters': dict(interface)})
        return interface

    for name in trees:
        infer_interface(name)
    made, role_counts, seen_objects, seen_values = {}, Counter(), {}, {}
    data_terms = {row['id']: row for row in tbox['data_properties']}

    def refs(nodes):
        return sorted({source[node] for node in nodes})

    missing_roles = {(row['kind'], row['role']) for row in missing}

    def term(kind, role):
        identifier = mapping[kind].get(role)
        if identifier is None and (kind, role) not in missing_roles:
            missing_roles.add((kind, role))
            missing.append({'kind': kind, 'role': role, 'term_id': None})
        return identifier if identifier in selected[kind] else None

    def add(identifier, role, label, *nodes):
        cls = term('classes', role)
        if cls is None:
            report['skipped_instances'].append({'id': identifier, 'role': role, 'source_refs': refs(nodes)})
            return identifier
        if identifier in made:
            row = made[identifier]
            if row['class_ids'] != [cls]:
                fail('XML 节点身份冲突，无法复用同一实例标识。', *nodes)
            row['source_refs'] = sorted(set(row['source_refs']) | set(refs(nodes)))
        else:
            row = {'id': identifier, 'label': label, 'class_ids': [cls], 'source_refs': refs(nodes)}
            result['individuals'].append(row)
            made[identifier] = row
            role_counts[role] += 1
        return identifier

    def assertion(subject, role, value, nodes, data=False):
        kind = 'data_properties' if data else 'object_properties'
        prop = term(kind, role)
        if prop is None or subject not in made or not data and value not in made:
            report['skipped_assertions'].append({'subject': subject, 'role': role, 'kind': kind,
                'value': value, 'source_refs': refs(nodes), 'reason': '未选入映射术语或关联实例未创建。'})
            return
        if data and not matches_domain(tbox, made[subject]['class_ids'],
                                       data_terms[prop]['domain_ids'] or data_terms[prop].get('class_ids', [])):
            report['skipped_assertions'].append({'subject': subject, 'role': role, 'kind': kind,
                'value': value, 'source_refs': refs(nodes),
                'reason': '主体类型不属于所选属性的适用类别（domain_ids/class_ids）；未越界挂载。'})
            return
        seen = seen_values if data else seen_objects
        identity = (subject, prop, type(value), value)
        if identity in seen:
            seen[identity]['source_refs'] = sorted(set(seen[identity]['source_refs']) | set(refs(nodes)))
            return
        row = {'subject': subject, 'property': prop, 'value' if data else 'object': value,
               'source_refs': refs(nodes)}
        result['data_assertions' if data else 'object_assertions'].append(row)
        seen[identity] = row

    def link(subject, role, obj, *nodes):
        assertion(subject, role, obj, nodes)

    def literal(subject, role, value, *nodes):
        assertion(subject, role, value, nodes, data=True)

    def optional_literal(subject, role, value, *nodes):
        if mapping['data_properties'].get(role) in selected['data_properties']:
            literal(subject, role, value, *nodes)

    def selected_role(kind, role):
        return mapping[kind].get(role) in selected[kind]

    def skill(name):
        return _identifier('skill', name)

    def owner(name):
        return skill(name) if name in callers else _identifier('procedure', name)

    def parameter(name, key):
        return _identifier('parameter', skill(name), key)

    def reference(name, key):
        return _identifier('data', name, key)

    def add_ports(name, implementation, parameter_sources):
        for key, direction in directions[name].items():
            nodes = parameter_sources(key)
            param = add(parameter(name, key), 'parameter', name+'.'+key, *nodes)
            link(skill(name), 'input_parameter' if direction == 'input' else 'output_parameter', param, *nodes)
            literal(param, 'parameter_name', key, *nodes)
            literal(param, 'parameter_direction', direction, *nodes)
            if key in ports.get(name, {}):
                port = ports[name][key][1]
                contract = add(_identifier('port', name, key), 'port', name+'.'+key+' 端口', port)
                link(implementation, 'port_contract', contract, port)
                link(param, 'parameter_port', contract, port)
                literal(contract, 'parameter_name', key, port)
                literal(contract, 'parameter_direction', direction, port)
                if port.get('type') is not None:
                    literal(contract, 'cpp_type', port.get('type'), port)
                    literal(param, 'cpp_type', port.get('type'), port)
                for target in (contract, param):
                    optional_literal(target, 'default_present', 'default' in port.attrib, port)
                    if 'default' in port.attrib:
                        optional_literal(target, 'declared_default', port.get('default'), port)
                for field, value in [*[(k, v) for k, v in port.attrib.items() if k not in ('name', 'type') and
                                       not (k == 'default' and mapping['data_properties'].get('declared_default') in selected['data_properties'])],
                                     *([('text', port.text)] if port.text and port.text.strip() else [])]:
                    report['omitted_information'].append({'source_ref': source[port], 'field': field,
                        'value': value, 'reason': '映射未声明该接口元数据属性；原始证据保留。'})

    for name in sorted(atomic_names):
        nodes = [models[name]] if name in models else builtin_nodes
        add(skill(name), 'atomic_skill', name, *nodes)
        implementation = _identifier('implementation', name)
        if name in models:
            add(implementation, 'implementation', name+' 节点实现', models[name])
            link(skill(name), 'implementation', implementation, models[name])
            optional_literal(implementation, 'node_kind', models[name].tag, models[name])
            optional_literal(implementation, 'source_model_xml', _model_xml(models[name]), *models[name].iter())
        add_ports(name, implementation, lambda key, name=name: [ports[name][key][1]] if name in models else builtin_nodes)
    for name, tree in trees.items():
        add(owner(name), 'composite_skill' if name in callers else 'procedure', name, tree)
        if name in callers:
            implementation = add(_identifier('subtree_implementation', name), 'implementation', name+' 子树实现', tree)
            link(owner(name), 'implementation', implementation, tree)
            optional_literal(implementation, 'node_kind', 'SubTree', *callers[name])
            if name in models and models[name].tag == 'SubTree':
                optional_literal(implementation, 'source_model_xml', _model_xml(models[name]), *models[name].iter())
            add_ports(name, implementation, lambda key, name=name: [ports[name][key][1]] if key in ports.get(name, {})
                      else [node for node in callers[name] if key in node.attrib])
        keys = defaultdict(list)
        for tree_id, _, node in calls:
            if tree_id != name:
                continue
            for raw in node.attrib.values():
                match = VARIABLE.fullmatch(raw)
                if match:
                    keys[match.group(1)].append(node)
            if node.tag == 'SetBlackboard':
                keys[node.get('output_key')].append(node)
        for key in directions[name]:
            if name in callers and not keys[key]:
                keys[key].extend([ports[name][key][1]] if key in ports.get(name, {})
                                 else [node for node in callers[name] if key in node.attrib])
        for key, nodes in sorted(keys.items()):
            data = add(reference(name, key), 'data_reference', name+'.'+key, *nodes)
            literal(data, 'data_scope', name, tree)
            literal(data, 'data_key', key, *nodes)
        if name in callers:
            for key in directions[name]:
                link(parameter(name, key), 'parameter_data', reference(name, key), *keys[key])
    sites, bindings = [], []
    for name, index, node in calls:
        tag = node.get('ID') if node.tag == 'SubTree' else node.tag
        call = add(_identifier('call', name, index), 'invocation', f'{name}/{index} '+(node.get('name') or tag), node)
        link(owner(name), 'contains_step', call, node)
        link(call, 'invokes_skill', skill(tag), node)
        literal(call, 'step_index', index, node)
        optional_literal(call, 'node_kind', models[tag].tag if tag in models else node.tag,
                         *([node, models[tag]] if tag in models else [node]))
        sites.append({'tree_id': name, 'step_index': index, 'source_ref': source[node], 'id': call,
                      'emitted': call in made})
        if (node.tag == 'SubTree' and set(PERCEPTION_FIELDS) <= set(node.attrib)
                and selected_role('classes', 'perception_profile')
                and any(selected_role('data_properties', role) for role in PERCEPTION_FIELDS.values())):
            profile = add(_identifier('perception_profile', name, index), 'perception_profile',
                          f'{name}/{index} '+tag+' 感知配置', node)
            object_name = node.get('object_name')
            if object_name and not VARIABLE.fullmatch(object_name) and selected_role('classes', 'part_type'):
                part = add(_identifier('part_type', object_name), 'part_type', '识别对象类型 '+object_name, node)
                optional_literal(part, 'object_name', object_name, node)
                if selected_role('object_properties', 'perception_profile'):
                    link(part, 'perception_profile', profile, node)
            for field, role in PERCEPTION_FIELDS.items():
                raw = node.get(field)
                if VARIABLE.fullmatch(raw):
                    report['omitted_information'].append({'source_ref': source[node], 'field': field,
                        'value': raw, 'reason': '感知配置参数引用变量，未将变量名猜作实际属性值。'})
                else:
                    optional_literal(profile, role, raw, node)
        for key, raw in node.attrib.items():
            if key in ('ID', 'name'):
                continue
            direction = directions[tag][key]
            binding = add(_identifier('binding', name, index, key), 'binding', f'{name}/{index}.{key}', node)
            link(call, 'binding', binding, node)
            link(binding, 'parameter', parameter(tag, key), node)
            literal(binding, 'binding_direction', direction, node)
            optional_literal(binding, 'raw_value', raw, node)
            match = VARIABLE.fullmatch(raw)
            if direction == 'output':
                if match is None:
                    fail(f'{name}/{index}.{key} 的输出不是静态数据引用。', node)
                link(binding, 'output_data_target', reference(name, match.group(1)), node)
            elif match:
                variable = match.group(1)
                if name in callers and directions[name].get(variable) == 'input':
                    link(binding, 'input_parameter_source', parameter(name, variable), node)
                else:
                    link(binding, 'input_data_source', reference(name, variable), node)
            else:
                literal(binding, 'literal_value', raw, node)
                if name in callers:
                    config = add(_identifier('fixed', name), 'fixed_configuration', name+' 固定实现配置', node)
                    link(_identifier('subtree_implementation', name), 'fixed_configuration', config, node)
                    link(config, 'fixed_binding', binding, node)
            bindings.append({'tree_id': name, 'step_index': index, 'parameter': key, 'direction': direction,
                             'raw_value': raw, 'source_ref': source[node], 'id': binding, 'emitted': binding in made})
    used = {ref for group in ('individuals', 'object_assertions', 'data_assertions')
            for row in result[group] for ref in row['source_refs']}
    report['counts'] = {'individuals': len(result['individuals']), 'object_assertions': len(result['object_assertions']),
                        'data_assertions': len(result['data_assertions']), 'roles': dict(role_counts)}
    report['source_coverage'] = {'trees_total': len(trees), 'calls_total': len(calls),
        'calls_emitted': sum(row['emitted'] for row in sites), 'bindings_total': len(bindings),
        'bindings_emitted': sum(row['emitted'] for row in bindings), 'call_sites': sites, 'binding_sites': bindings,
        'evidence_total': len(evidence), 'evidence_used': len(used),
        'unrepresented_evidence_refs': [row['id'] for row in evidence if row['id'] not in used],
        'declared_ports_total': sum(len(ports.get(name, {})) for name in atomic_names | set(callers)),
        'declared_ports_emitted': role_counts['port']}
    report['excluded_model_ids'] = sorted(set(models)-active-set(callers)) if not include_unused else []
    property_counts = Counter(row['property'] for row in result['data_assertions'])
    report['property_coverage'] = []
    for row in tbox['data_properties']:
        domains = row['domain_ids'] or row.get('class_ids', [])
        has_subject = any(matches_domain(tbox, item['class_ids'], domains) for item in made.values())
        reason = ''
        if not property_counts[row['id']]:
            if not has_subject:
                reason = '当前 XML 规则结果中没有该属性所属类别的实例；未据其他类别的值补造。'
            elif row['id'] not in mapping['data_properties'].values():
                reason = '该已选属性尚未配置对应的 XML 语义抽取规则。'
            else:
                reason = '当前对应主体没有可按已配置规则抽取的声明值，未猜测或补造。'
        report['property_coverage'].append({'id': row['id'], 'label': row['label'],
            'assertions': property_counts[row['id']], 'domain_ids': row['domain_ids'],
            'class_ids': row.get('class_ids', []), 'reason': reason})
    undeclared = [row['id'] for row in tbox['data_properties'] if not row['domain_ids']]
    report['tbox_warnings'] = [{'code': 'undeclared_data_property_domains', 'property_ids': undeclared,
        'message': f'{len(undeclared)} 个已选数据属性未声明 domain_ids；仅按 class_ids 筛选抽取主体，'
                   '保留上传骨架，不补写 OWL 定义域。'}] if undeclared else []
    if builtin_nodes:
        result['clarifications'].append('SetBlackboard 按内建语义读取 value/output_key 两个输入参数；XML 未声明其端口、C++类型或实现，未补造。')
    if report['inferred_interfaces']:
        inferred = [row['tree_id'] for row in report['inferred_interfaces'] if not row['declared']]
        if inferred:
            result['clarifications'].append('以下子树公开接口由调用实参和内部读写确定：'+ '、'.join(inferred)+'；未猜测未声明的数据类型。')
    if missing or report['skipped_assertions'] or report['skipped_instances']:
        result['clarifications'].append(f'部分角色未选入或属性归属不匹配；略过 {len(report["skipped_instances"])} 项实例、{len(report["skipped_assertions"])} 项断言，详见构建报告。')
    if report['omitted_information']:
        result['clarifications'].append('未选入、未配置映射的元数据及未解析的变量值未补写为属性；原始内容和逐项省略原因保留在来源及构建报告。')
    if undeclared:
        result['clarifications'].append(report['tbox_warnings'][0]['message'])
    result['clarifications'].append('本次规则覆盖流程、接口、调用、参数、绑定、作用域变量、固定配置和有明确字面量来源的感知配置；逐属性输出数量与缺口见构建报告。')
    result['reply'] = f'已按 XML 规则构建 {len(result["individuals"])} 个实例、{len(result["object_assertions"])} 项对象关系和 {len(result["data_assertions"])} 项属性值；未调用模型抽取。'
    return result, report
