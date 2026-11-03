"""Explicit synthetic business knowledge for offline demonstration — DR-061 / DR-065.

The supplement never replaces a graph or edits executable template definitions.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from rdflib import Literal, RDF, XSD

from .compiler import instantiate
from .graph import S, ONTOLOGY, value
from .semantics import xml_from_graph, node_models_from_graph, configuration_paths

FIELDS = {"schema", "mode", "description", "source_sha256", "models_sha256", "entities",
          "perception_profile", "placement_profile", "config", "evidence_reference", "valid_until", "task"}


def apply_demo_supplement(graph, path):
    """Return an independent demo graph, the input digest and an RDF change list."""
    content = Path(path).read_bytes()
    data = json.loads(content.decode("utf-8-sig"))
    if not isinstance(data, dict) or set(data) != FIELDS:
        raise ValueError("演示补充资料字段不完整或包含未知字段。")
    if data["schema"] != "robotics-demo-supplement-v1" or data["mode"] != "demo":
        raise ValueError("只接受明确标记为 demo 的 v1 合成补充资料。")
    for key, source in (("source_sha256", S.BehaviorTree_Source), ("models_sha256", S.NodeModels_Source)):
        digest = data[key]
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or digest != value(graph, source, "sha256"):
            raise ValueError(f"演示补充资料与本轮来源不匹配：{key}")
    reference = data["evidence_reference"]
    if not isinstance(reference, str) or not reference.startswith("demo-only:") or not reference[10:].strip():
        raise ValueError("模拟依据必须明确使用 demo-only: 标记，不能充当物理证据。")
    try:
        expiry = datetime.fromisoformat(data["valid_until"].replace("Z", "+00:00"))
        if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
            raise ValueError("过期或无时区")
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("演示适配记录必须有带时区的有效截止时间。") from exc

    def entity(name, kind):
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError("补充资料只能引用当前领域的已登记实体标识。")
        item = S[name]
        if (item, RDF.type, S[kind]) not in graph:
            raise ValueError(f"未登记的 {kind}：{name}")
        return item

    kinds = {"part": "Workpiece", "part_type": "PartType", "station": "Workstation",
             "carrier": "Carrier", "slot": "PlacementSlot"}
    if not isinstance(data["entities"], dict) or set(data["entities"]) != set(kinds):
        raise ValueError("业务对象集合不完整。")
    entities = {key: entity(data["entities"][key], kind) for key, kind in kinds.items()}
    for left, prop, right in (("part", "hasPartType", "part_type"), ("station", "hasCarrier", "carrier"),
                              ("carrier", "hasSlot", "slot")):
        if (entities[left], S[prop], entities[right]) not in graph:
            raise ValueError("补充对象不属于已有业务关系，不能猜测或重写关联。")
    perception = entity(data["perception_profile"], "PerceptionProfile")
    placement = entity(data["placement_profile"], "PlacementProfile")
    joints = graph.value(perception, S.hasCaptureJoints, any=False)
    pose = graph.value(placement, S.hasTargetPose, any=False)
    if (joints, RDF.type, S.JointConfiguration) not in graph or (pose, RDF.type, S.CartesianPose) not in graph:
        raise ValueError("Profile 缺少已登记的关节配置或位姿。")
    template = xml_from_graph(graph)
    instantiate(template, data["config"], baseline=template, models_root=node_models_from_graph(graph),
                config_paths=configuration_paths(graph))

    protected_types = ("AtomicSkill", "CompositeSkill", "TaskProcedure", "SkillInvocation", "SkillParameter",
                       "ParameterBinding", "PortContract", "DataReference", "SkillCondition", "SkillImplementation")
    protected = {S.BehaviorTree_Source, S.NodeModels_Source}
    for kind in protected_types:
        protected.update(graph.subjects(RDF.type, S[kind]))
    result = deepcopy(graph)

    def set_value(subject, prop, raw):
        if subject in protected:
            raise ValueError("演示资料不能修改技能、模板、绑定、条件或来源。")
        result.set((subject, S[prop], Literal(raw, datatype=XSD.string)))

    set_value(ONTOLOGY, "datasetKind", "demo")
    for item in (*entities.values(), perception, placement, joints, pose):
        set_value(item, "confirmationStatus", "simulated")
        set_value(item, "evidenceReference", reference)
    result.set((entities["part_type"], S.hasPerceptionProfile, perception))
    result.set((entities["slot"], S.hasPlacementProfile, placement))
    for field, prop in (("object_name", "objectName"), ("model_name", "modelName"),
                        ("threshold", "threshold"), ("exposure_time", "exposureTime")):
        set_value(perception, prop, data["config"]["Perception"][field])
    set_value(joints, "rawValue", data["config"]["Perception"]["position"])
    set_value(pose, "rawValue", data["config"]["Place"]["place_pose"])
    for kind, implementation, name in (("grasp_compatibility", S.grasp_Subtree_Impl, "Demo_Grasp_Compatibility"),
                                       ("placement_compatibility", S.Place_Subtree_Impl, "Demo_Placement_Compatibility")):
        evidence = S[name]
        if any(graph.triples((evidence, None, None))):
            raise ValueError(f"模拟证据标识已存在，不能覆盖：{name}")
        result.add((evidence, RDF.type, S.ReadinessEvidence))
        for prop, raw in (("evidenceKind", kind), ("confirmationStatus", "simulated"),
                          ("sourceHash", data["source_sha256"]), ("evidenceReference", reference),
                          ("validUntil", data["valid_until"])):
            set_value(evidence, prop, raw)
        result.add((evidence, S.forImplementation, implementation))
        result.add((evidence, S.forPartType, entities["part_type"]))
        if kind == "placement_compatibility":
            result.add((evidence, S.forSlot, entities["slot"]))
    changes = [{"operation": operation, "subject": subject.n3(), "predicate": prop.n3(), "object": obj.n3()}
               for operation, triples in (("remove", set(graph) - set(result)), ("add", set(result) - set(graph)))
               for subject, prop, obj in sorted(triples, key=lambda triple: tuple(map(str, triple)))]
    return {"graph": result, "supplement_sha256": hashlib.sha256(content).hexdigest(), "changes": changes,
            "task": deepcopy(data["task"]), "config": deepcopy(data["config"])}
