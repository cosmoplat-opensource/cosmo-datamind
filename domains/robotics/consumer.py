"""Load and revalidate a self-contained OWL snapshot — DR-060 / DR-065.

Publication validity does not confirm business mappings or execution readiness.
"""
import hashlib
from pathlib import Path
from xml.sax import SAXException

from rdflib import Graph, RDF
from rdflib.compare import to_isomorphic
from rdflib.exceptions import Error as RDFError

from .artifact_contract import ARTIFACT_CONTRACT, VALIDATION_SHAPES_SHA256, CONDITIONS, graph_validation
from .graph import S, ONTOLOGY, value
from .semantics import xml_from_graph, node_models_from_graph, configuration_paths
from .validator import validate_tree


def check_mode(graph, mode):
    from .evidence import policy
    policy(graph)  # DR-061: an unknown policy cannot silently use the legacy gate.
    if mode not in ("real", "demo"):
        raise ValueError("mode 必须为 real 或 demo。")
    kinds = list(graph.objects(ONTOLOGY, S.datasetKind))
    if len(kinds) > 1 or (kinds and str(kinds[0]) not in ("real", "demo")):
        raise ValueError("datasetKind 必须为唯一的 real 或 demo 标记。")
    demo = bool(kinds and str(kinds[0]) == "demo")
    simulated = any(str(item) == "simulated" for item in graph.objects(None, S.confirmationStatus))
    if mode == "real" and (demo or simulated):
        raise ValueError("真实模式不能使用演示图谱或模拟状态。")
    if mode == "demo" and not demo:
        raise ValueError("演示模式只能使用明确标记 datasetKind=demo 的独立图谱。")


def validate_graph(graph, *, mode="real"):
    """Validate graph semantics before it becomes a consumable artifact."""
    check_mode(graph, mode)
    conforms, report, detail = graph_validation(graph)
    if not conforms:
        raise ValueError(str(detail))
    try:
        template = xml_from_graph(graph)
        models = node_models_from_graph(graph)
        paths = configuration_paths(graph)
        errors = validate_tree(template, models, baseline=template, config_paths=paths)
    except (RDFError, KeyError, TypeError, IndexError) as exc:
        raise ValueError(f"本体执行结构不完整：{exc}") from exc
    # An unresolved authoring value is not permission to generate a strict candidate.
    errors = [error for error in errors if error["code"] != "unresolved_parameter"]
    if errors:
        raise ValueError(f"本体执行结构或数据流不兼容：{errors}")
    counts = {cls: len(set(graph.subjects(RDF.type, S[cls]))) for cls in
              ("AtomicSkill", "CompositeSkill", "SkillInvocation", "SkillParameter",
               "DataReference", "ParameterBinding", "ImplementationConfiguration")}
    return {
        "source_sha256": value(graph, S.BehaviorTree_Source, "sha256"),
        "models_sha256": value(graph, S.NodeModels_Source, "sha256"),
        "ontology_counts": counts, "shacl_conforms": True,
        "source_xml_required": False, "external_artifacts_required": False,
        "artifact_contract": ARTIFACT_CONTRACT,
        "validation_shapes_sha256": VALIDATION_SHAPES_SHA256,
        "condition_contracts_checked": len(CONDITIONS),
        "execution_readiness": "not_verified",
        "reasoner": {"status": "not_verified_for_current_ontology",
                     "message": "未执行 OWL 推理，不读取相邻的推理报告。"},
    }, report


def load_ontology(path, *, mode="real"):
    """Read one immutable byte snapshot; never resolve source XML or sidecars."""
    path = Path(path)
    if path.suffix.lower() != ".owl":
        raise ValueError("本体输入只接受自包含的 .owl 文件（UTF-8 RDF/XML）。")
    return load_ontology_bytes(path.read_bytes(), mode=mode)


def load_ontology_bytes(content, *, mode="real"):
    """Validate explicit bytes without requiring a published asset or mutable path (DR-060)."""
    if not isinstance(content, bytes) or not content:
        raise ValueError("本体输入必须为非空 bytes。")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("本体必须使用 UTF-8 编码。") from exc
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        raise ValueError("本体 RDF/XML 不能包含 DTD 或实体声明。")
    try:
        graph = Graph().parse(data=text, format="xml")
    except (SAXException, RDFError) as exc:
        raise ValueError(f"无法解析本体 RDF/XML：{exc}") from exc
    validation, report = validate_graph(graph, mode=mode)
    validation["ontology_sha256"] = hashlib.sha256(content).hexdigest()
    return {"graph": graph, "graph_digest": to_isomorphic(graph).graph_digest(),
            "validation": validation, "shacl_report": report.serialize(format="turtle")}
