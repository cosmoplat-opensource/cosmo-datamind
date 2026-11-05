"""Trusted publication protocol and SHACL checks — DR-060 / DR-065.

Validation migrated from the hash-verified yang agent; source details in IR-016.
"""
import hashlib
from pathlib import Path
from rdflib import BNode, Graph, Literal, Namespace, RDF, OWL, XSD
from rdflib.collection import Collection
from rdflib.plugins.sparql import prepareQuery
from pyshacl import validate
from .graph import S, ONTOLOGY, value
from .profiles.ur_pick_place import CONDITION_SPECS

ARTIFACT_CONTRACT = "ur-pick-place-owl-v2"
VALIDATION_SHAPES_SHA256 = "b57e61b049b0a521cf1812987a24522178412867e6d358571a8a0658c6021ae2"
CONDITIONS = {name: {"kind": kind, "mechanism": mechanism, "rule": rule, "skills": skills}
              for name, kind, label, mechanism, rule, skills in CONDITION_SPECS}

class _ValidationGraph(Graph):
    """DR-063: prepare repeated SHACL queries once, execute every focus binding.

    This graph is an isolated copy owned by one validation. No validation result,
    mutable source graph, query bindings or global RDFLib behavior is cached.
    """
    def __init__(self):
        super().__init__()
        self._prepared = {}

    def query(self, query_object, processor='sparql', result='sparql', initNs=None,
              initBindings=None, use_store_provided=True, **kwargs):
        namespaces = initNs or dict(self.namespaces())
        if isinstance(query_object, str) and processor == 'sparql':
            key = (query_object, tuple(sorted(namespaces.items())), kwargs.get('base'))
            prepared = self._prepared.get(key)
            if prepared is None:
                prepared = prepareQuery(query_object, initNs=namespaces, base=kwargs.get('base'))
                if len(self._prepared) < 32: self._prepared[key] = prepared
            query_object = prepared
        return super().query(query_object, processor=processor, result=result, initNs=namespaces,
                             initBindings=initBindings, use_store_provided=use_store_provided, **kwargs)


def trusted_shapes():
    """Load only the shipped publisher resource, never a neighboring sidecar."""
    content = (Path(__file__).parent / "resources/ur_pick_place_v2.ttl").read_bytes()
    if hashlib.sha256(content).hexdigest() != VALIDATION_SHAPES_SHA256:
        raise ValueError("发布器规则资源与协议版本不匹配。")
    return content.decode("utf-8")


def graph_validation(g):
    if any(g.triples((None, OWL.imports, None))):
        raise ValueError("本体输入必须自包含，不能声明 owl:imports 或依赖外部文件。")
    for prop in ("artifactContract", "validationShapes"):
        items = list(g.objects(ONTOLOGY, S[prop]))
        if len(items) != 1 or not isinstance(items[0], Literal) or items[0].language or items[0].datatype not in (None, XSD.string):
            raise ValueError(f"本体缺少唯一的字符串 {prop}，请由 ontology 项目重新导出 .owl。")
    if value(g, ONTOLOGY, "artifactContract") != ARTIFACT_CONTRACT:
        raise ValueError(f"不支持的本体输入协议；需要 {ARTIFACT_CONTRACT}，请重新导出 .owl。")
    snapshot = value(g, ONTOLOGY, "validationShapes")
    if hashlib.sha256(snapshot.encode("utf-8")).hexdigest() != VALIDATION_SHAPES_SHA256:
        raise ValueError("本体内嵌校验规则不在 agent 支持的白名单中；请匹配导出与 agent 版本。")
    shapes = Graph().parse(data=snapshot, format="turtle")
    sh = Namespace("http://www.w3.org/ns/shacl#")
    registered = BNode()
    shapes.add((registered, RDF.type, sh.NodeShape))
    shapes.add((registered, sh.targetClass, S.SkillCondition))
    members = BNode()
    Collection(shapes, members, [S[name] for name in CONDITIONS])
    shapes.add((registered, sh["in"], members))
    relations = {"precondition": S.hasPrecondition, "applicability": S.hasApplicabilityCondition, "expected_effect": S.hasExpectedEffect}
    for name, spec in CONDITIONS.items():
        shape = BNode()
        shapes.add((shape, RDF.type, sh.NodeShape))
        shapes.add((shape, sh.targetNode, S[name]))
        shapes.add((shape, sh["class"], S.SkillCondition))
        for prop, raw in (("conditionKind", spec["kind"]), ("checkMechanism", spec["mechanism"]), ("ruleId", spec["rule"])):
            constraint = BNode()
            shapes.add((shape, sh.property, constraint))
            shapes.add((constraint, sh.path, S[prop]))
            shapes.add((constraint, sh.hasValue, Literal(raw, datatype=XSD.string)))
            shapes.add((constraint, sh.maxCount, Literal(1)))
        for kind, relation in relations.items():
            constraint, inverse = BNode(), BNode()
            skills = [S[skill] for skill in spec["skills"]] if kind == spec["kind"] else []
            allowed = BNode() if skills else RDF.nil
            if skills:
                Collection(shapes, allowed, skills)
            shapes.add((shape, sh.property, constraint))
            shapes.add((constraint, sh.path, inverse))
            shapes.add((inverse, sh.inversePath, relation))
            shapes.add((constraint, sh["in"], allowed))
            shapes.add((constraint, sh.minCount, Literal(len(skills))))
            shapes.add((constraint, sh.maxCount, Literal(len(skills))))
    # Preserve caller RDF exactly: only this private copy receives RDFS inference.
    working = _ValidationGraph()
    for prefix, namespace in g.namespaces(): working.bind(prefix, namespace)
    for triple in g: working.add(triple)
    conforms, report, text = validate(working, shacl_graph=shapes, inference="rdfs", advanced=True, inplace=True)
    return bool(conforms), report, text
