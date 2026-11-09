"""Read-only RDF instance and executable structure projections — DR-068."""
from rdflib import Literal, OWL, RDF, RDFS, URIRef

from .graph import CLASSES, S
from .semantics import xml_from_graph

BUSINESS = {'Workpiece', 'PartType', 'Workstation', 'Carrier', 'PlacementSlot',
            'PerceptionProfile', 'PlacementProfile', 'CartesianPose', 'JointConfiguration',
            'CoordinateFrame', 'CalibrationTransform', 'RobotArm', 'Gripper', 'Camera', 'Resource'}
SKILLS = {'AtomicSkill', 'CompositeSkill', 'TaskProcedure', 'SkillInvocation', 'SkillImplementation'}


def local(term):
    return str(term).rsplit('#', 1)[-1].rsplit('/', 1)[-1]


def label(graph, term):
    labels = sorted(graph.objects(term, RDFS.label), key=lambda x: (x.language != 'zh', str(x)))
    return str(labels[0]) if labels else CLASSES.get(local(term), local(term))


def relations(graph, *, max_nodes=3000, max_edges=10000):
    subjects = {s for s in graph.subjects(RDF.type, OWL.NamedIndividual) if isinstance(s, URIRef)}
    # Imported graphs need not repeat owl:NamedIndividual for each typed instance.
    for kind in CLASSES:
        subjects.update(s for s in graph.subjects(RDF.type, S[kind]) if isinstance(s, URIRef))
    subjects = {s for s in subjects if (s, RDF.type, OWL.Class) not in graph}
    if len(subjects) > max_nodes:
        raise ValueError('本体实例规模超过视图上限，请缩小本体范围。')
    nodes, edges = [], []
    for subject in sorted(subjects, key=str):
        kinds = sorted(local(t) for t in graph.objects(subject, RDF.type) if t != OWL.NamedIndividual)
        group = 'business' if BUSINESS.intersection(kinds) else 'skills' if SKILLS.intersection(kinds) else 'other'
        properties = [{'name': label(graph, p), 'predicate': str(p), 'value': str(o)}
                      for p, o in sorted(graph.predicate_objects(subject), key=lambda row: tuple(map(str, row)))
                      if isinstance(o, Literal) and local(p) not in {'sourceNodeXML', 'embeddedModelXML'}]
        nodes.append({'id': str(subject), 'label': label(graph, subject), 'types': kinds,
                      'type_labels': [CLASSES.get(t, t) for t in kinds], 'group': group, 'properties': properties})
    for s, p, o in sorted(graph, key=lambda row: tuple(map(str, row))):
        if s in subjects and o in subjects and p != RDF.type:
            edges.append({'id': f'edge-{len(edges)}', 'source': str(s), 'target': str(o),
                          'predicate': str(p), 'label': label(graph, p)})
            if len(edges) > max_edges:
                raise ValueError('本体关系规模超过视图上限，请缩小本体范围。')
    return {'nodes': nodes, 'edges': edges}


def behavior_tree(graph):
    if len(set(graph.subjects(RDF.type, S.SkillInvocation))) > 3000:
        raise ValueError('行为树规模超过视图上限。')
    try:
        root = xml_from_graph(graph)
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError('当前本体的流程或参数绑定不完整，无法显示行为树。') from exc
    count = 0

    def project(node, path):
        nonlocal count
        count += 1
        if count > 3000:
            raise ValueError('行为树规模超过视图上限。')
        return {'id': path, 'tag': node.tag, 'attributes': dict(sorted(node.attrib.items())),
                'children': [project(child, f'{path}/{i}') for i, child in enumerate(node)]}

    trees = [{'name': tree.get('ID'), 'root': project(tree, f'tree/{i}')}
             for i, tree in enumerate(root) if tree.tag == 'BehaviorTree']
    if not trees or len({t['name'] for t in trees}) != len(trees) or any(not t['name'] for t in trees):
        raise ValueError('本体行为树缺失或标识重复。')
    if root.get('main_tree_to_execute') not in {t['name'] for t in trees}:
        raise ValueError('本体缺少入口行为树。')
    return {'main_tree': root.get('main_tree_to_execute'), 'trees': trees}
