"""Present confirmed application class pairs using the application IRI — DR-189."""
import json

from domains.robotics.graph import CLASSES, S


def confirmed_class_aliases(role_map, declared):
    if not role_map or role_map.get('confirmed') is not True or role_map.get('version') != '1':
        return {}, set()
    aliases = {}
    for binding in role_map['bindings']:
        if binding['kind'] == 'classes' and binding['role'] in CLASSES:
            source, target = binding['iri'], str(S[binding['role']])
            if source != target and source in declared and target in declared:
                aliases[source] = target
    # An IRI used on both sides of distinct bindings has no unique application representative.
    overlap = aliases.keys() & set(aliases.values())
    if overlap:
        aliases = {k: v for k, v in aliases.items() if k not in overlap and v not in overlap}
    return aliases, overlap


def group_application_classes(view, role_map, declared):
    aliases, overlap = confirmed_class_aliases(role_map, declared)
    if overlap:
        view['warnings'].append('部分角色映射的标识重叠，相关类别按原标识分别展示。')
    if not aliases:
        return view
    nodes = {node['id']: node for node in view['nodes']}
    for source, target in aliases.items():
        design = nodes.pop(source)
        app = nodes[target]
        app.setdefault('design_classes', []).append({k: v for k, v in design.items() if k != 'individuals'})
        app['individuals'] = sorted(set(app['individuals']) | set(design['individuals']))
    view['nodes'] = list(nodes.values())

    def project_terms(entity, field, *, keep_original=False):
        terms = entity[field]
        if any(t in aliases for t in terms):
            if keep_original:
                entity['original_' + field] = terms
            entity[field] = sorted({aliases.get(t, t) for t in terms})

    editable = view['context']['kind'] == 'edit-copy'
    for person in view['individuals']:
        project_terms(person, 'types', keep_original=editable)
    for prop in view['properties']:
        for field in ('domains', 'ranges'):
            project_terms(prop, field, keep_original=editable)
    for node in view['nodes']:
        if 'expression' in node:
            project_terms(node['expression'], 'operands')

    edges = {}
    for edge in view['edges']:
        source, target = edge['source'], edge['target']
        key = (edge['kind'], aliases.get(source, source), edge['predicate'], aliases.get(target, target))
        if key not in edges:
            edges[key] = {**edge, 'source': key[1], 'target': key[3],
                          'id': json.dumps(key, ensure_ascii=False, separators=(',', ':'))}
        if editable and edge['kind'] in ('subclass', 'disjoint', 'equivalent'):
            edges[key].setdefault('assertion_pairs', []).append([source, target])
    view['edges'] = list(edges.values())
    return view
