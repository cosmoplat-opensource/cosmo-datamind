"""Deterministically import supported BT XML into RDF — DR-060.

Extraction migrated from yang ontology_pipeline; all inputs are explicit snapshots.
"""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from rdflib import RDF
from .graph import S, schema, individual, literal, parameter, reference, invocation
from .profiles.ur_pick_place import COMPOSITES, ROLES, owner, semantic_type, add_domain, add_conditions
from .xml_contract import VAR
from .source_validation import parse_xml, validate_sources

def build_graph(source, models, *, profile="ur_pick_place"):
    if profile != "ur_pick_place":
        raise ValueError(f"Unsupported robotics profile: {profile}")
    source, models = Path(source), Path(models)
    source_bytes, model_bytes = source.read_bytes(), models.read_bytes()
    root = parse_xml(source_bytes, source_role="behavior_tree")
    catalog = parse_xml(model_bytes, source_role="node_models")
    validate_sources(root, catalog)
    hashes = {source: hashlib.sha256(source_bytes).hexdigest(),
              models: hashlib.sha256(model_bytes).hexdigest()}
    g = schema()
    for path, name in ((source, "BehaviorTree_Source"), (models, "NodeModels_Source")):
        item = individual(g, S[name], "SourceDocument", path.name)
        literal(g, item, "sourceFile", path.name)
        literal(g, item, "sha256", hashes[path])
    literal(g, S.BehaviorTree_Source, "rootAttributes", json.dumps(root.attrib, ensure_ascii=False))
    literal(g, S.BehaviorTree_Source, "embeddedModelXML", ET.tostring(root.find("TreeNodesModel"), encoding="unicode"))
    trees = {tree.get("ID"): tree for tree in root.findall("BehaviorTree")}
    registry = {node.get("ID"): node for node in catalog.find("TreeNodesModel")}
    embedded = {node.get("ID"): node for node in root.find("TreeNodesModel")}
    builtin = ET.fromstring('<Action ID="SetBlackboard"><input_port name="value" type="Any"/><input_port name="output_key" type="std::string"/></Action>')
    registry["SetBlackboard"] = builtin
    active = {n.tag for t in trees.values() for n in t[0] if n.tag != "SubTree"}
    for tag, model in registry.items():
        impl = individual(g, S[tag + "_BT_Impl"], "SkillImplementation", tag + " 节点实现")
        literal(g, impl, "xmlTag", tag)
        literal(g, impl, "nodeKind", model.tag)
        literal(g, impl, "sourceModelXML", ET.tostring(model, encoding="unicode"))
        g.add((impl, S.derivedFrom, S.NodeModels_Source if tag != "SetBlackboard" else S.BehaviorTree_Source))
        for port in model:
            name = port.get("name")
            pc = individual(g, S[f"Port_{tag}__{name}"], "PortContract", tag + "." + name)
            g.add((impl, S.hasPortContract, pc))
            literal(g, pc, "parameterName", name)
            literal(g, pc, "direction", port.tag.removesuffix("_port"))
            literal(g, pc, "cppType", port.get("type", "unspecified"))
            literal(g, pc, "defaultPresent", "default" in port.attrib)
            if "default" in port.attrib:
                literal(g, pc, "declaredDefault", port.get("default"))
            if port.text:
                literal(g, pc, "description", port.text.strip())
            if tag in embedded:
                ep = next(p for p in embedded[tag] if p.get("name") == name)
                literal(g, pc, "embeddedDefaultPresent", "default" in ep.attrib)
                if "default" in ep.attrib:
                    literal(g, pc, "embeddedDefault", ep.get("default"))
        if tag not in active:
            literal(g, impl, "description", "节点目录已声明，当前抓移放流程未调用；不自动开放生成。")
            continue
        skill = individual(g, S[tag + "_Skill"], "AtomicSkill", tag + " 原子技能")
        g.add((skill, RDF.type, S.Skill))
        g.add((skill, S.implementedBy, impl))
        for port in model:
            name, direction = port.get("name"), port.tag.removesuffix("_port")
            p = individual(g, parameter(tag + "_Skill", name), "SkillParameter", tag + "." + name)
            g.add((skill, S.hasInput if direction == "input" else S.hasOutput, p))
            g.add((p, S.mapsToPort, S[f"Port_{tag}__{name}"]))
            for key, val in {"parameterName": name, "direction": direction, "cppType": port.get("type", "unspecified"),
                             "semanticType": semantic_type(tag, name, port.get("type", ""))}.items():
                literal(g, p, key, val)
    for order, (tree_id, tree) in enumerate(trees.items(), 1):
        proc = owner(tree_id)
        individual(g, proc, "CompositeSkill" if tree_id in COMPOSITES else "TaskProcedure", tree_id + (" 复合技能" if tree_id in COMPOSITES else " 抓移放任务流程"))
        literal(g, proc, "treeId", tree_id)
        literal(g, proc, "treeOrder", order)
        literal(g, proc, "sourcePath", f"/root/BehaviorTree[@ID='{tree_id}']")
        g.add((proc, S.derivedFrom, S.BehaviorTree_Source))
        if tree_id in COMPOSITES:
            g.add((proc, RDF.type, S.Skill))
            impl = individual(g, S[tree_id + "_Subtree_Impl"], "SkillImplementation", tree_id + " 子树实现")
            literal(g, impl, "treeId", tree_id)
            g.add((proc, S.implementedBy, impl))
            config = individual(g, S[tree_id + "_FixedConfig"], "ImplementationConfiguration", tree_id + " 内部固定配置")
            g.add((impl, S.hasFixedConfiguration, config))
            skill_name, inputs, outputs = COMPOSITES[tree_id]
            for name in inputs + outputs:
                direction = "input" if name in inputs else "output"
                p = individual(g, parameter(skill_name, name), "SkillParameter", tree_id + "." + name)
                g.add((proc, S.hasInput if direction == "input" else S.hasOutput, p))
                literal(g, p, "parameterName", name)
                literal(g, p, "direction", direction)
                literal(g, p, "semanticType", "JointConfiguration" if name == "position" else "QuaternionPose" if name in ("pose", "obj_in_robot", "place_pose") else "RawInterfaceValue")
                literal(g, p, "interfaceOrigin", "explicit_subtree_model" if tree_id == "Perception" else "inferred_from_call_and_usage")
                literal(g, p, "isPublicConfig", direction == "input" and name != "obj_in_robot")
                g.add((p, S.parameterData, reference(tree_id, name)))
                if direction == "output":
                    g.add((p, S.exportsData, reference(tree_id, name)))
        # Include all 24 original scoped references, even those representing inputs.
        keys = {VAR.fullmatch(val).group(1) for n in tree[0] for val in n.attrib.values() if VAR.fullmatch(val)}
        keys.update(n.get("output_key") for n in tree[0] if n.tag == "SetBlackboard")
        for key in sorted(keys):
            data = individual(g, reference(tree_id, key), "DataReference", tree_id + "." + key)
            literal(g, data, "scope", tree_id)
            literal(g, data, "dataKey", key)
    for tree_id, tree in trees.items():
        for index, node in enumerate(tree[0], 1):
            call = individual(g, invocation(tree_id, index), "SkillInvocation", tree_id + f" / {index} " + ROLES[tree_id][index - 1])
            g.add((owner(tree_id), S.hasStep, call))
            g.add((call, S.inProcedure, owner(tree_id)))
            tag = node.get("ID") if node.tag == "SubTree" else node.tag
            skill_name = COMPOSITES[tag][0] if node.tag == "SubTree" else tag + "_Skill"
            g.add((call, S.invokesSkill, S[skill_name]))
            literal(g, call, "stepIndex", index)
            literal(g, call, "xmlTag", node.tag)
            literal(g, call, "sourcePath", f"/root/BehaviorTree[@ID='{tree_id}']/Sequence/*[{index}]")
            literal(g, call, "sourceNodeXML", ET.tostring(node, encoding="unicode"))
            g.add((call, S.derivedFrom, S.BehaviorTree_Source))
            if node.get("name"):
                literal(g, call, "xmlName", node.get("name"))
            if node.tag == "SubTree":
                directions = {n: "input" for n in COMPOSITES[tag][1]} | {n: "output" for n in COMPOSITES[tag][2]}
            else:
                directions = {p.get("name"): p.tag.removesuffix("_port") for p in registry[tag]}
            for name, raw in node.attrib.items():
                if name in ("ID", "name"):
                    continue
                direction = directions[name]
                binding = individual(g, S[f"{tree_id}_{index}__{name}"], "ParameterBinding", f"{tree_id}/{index}.{name}")
                g.add((call, S.hasBinding, binding))
                g.add((binding, S.bindsParameter, parameter(skill_name, name)))
                literal(g, binding, "bindingDirection", direction)
                literal(g, binding, "rawBinding", raw)
                match = VAR.fullmatch(raw)
                if direction == "output":
                    data = reference(tree_id, match.group(1))
                    g.add((binding, S.targetData, data))
                    g.add((call, S.producesData, data))
                elif match:
                    key = match.group(1)
                    if tree_id in COMPOSITES and key in COMPOSITES[tree_id][1]:
                        g.add((binding, S.sourceParameter, parameter(COMPOSITES[tree_id][0], key)))
                    else:
                        g.add((binding, S.sourceData, reference(tree_id, key)))
                else:
                    literal(g, binding, "literalValue", raw)
                    if tree_id in COMPOSITES:
                        g.add((S[tree_id + "_FixedConfig"], S.hasFixedBinding, binding))
                if node.tag == "SetBlackboard" and name == "output_key":
                    g.add((call, S.producesData, reference(tree_id, raw)))
    add_domain(g, root)
    add_conditions(g)
    return g
