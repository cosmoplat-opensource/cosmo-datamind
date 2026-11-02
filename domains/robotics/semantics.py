"""Read executable structure from RDF, never archived XML — DR-059 / DR-060.

Migrated from yang ontology_pipeline; no disk reads at import or reconstruction.
"""
import json
import xml.etree.ElementTree as ET
from rdflib import RDF
from .graph import S, value, reference

def parameter_contract(g, parameter):
    """Authoring and v2 publications share one authoritative port contract."""
    mapped = g.value(parameter, S.mapsToPort, any=False)
    return mapped if mapped is not None else parameter


def parameter_value(g, parameter, prop):
    return value(g, parameter_contract(g, parameter), prop)


def produced_data(g, call):
    """Derive writes from bindings; v2 need not duplicate producesData edges."""
    bindings = list(g.objects(call, S.hasBinding))
    result = {data for binding in bindings for data in g.objects(binding, S.targetData)}
    if (call, S.invokesSkill, S.SetBlackboard_Skill) in g:
        parent = g.value(None, S.hasStep, call, any=False)
        for binding in bindings:
            if parameter_value(g, g.value(binding, S.bindsParameter), "parameterName") == "output_key":
                result.add(reference(value(g, parent, "treeId"), value(g, binding, "literalValue")))
    return result


def xml_from_graph(g):
    """Reconstruct execution nodes from graph relations, not sourceNodeXML."""
    root = ET.Element("root", json.loads(value(g, S.BehaviorTree_Source, "rootAttributes")))
    procedures = set(g.subjects(RDF.type, S.CompositeSkill)) | set(g.subjects(RDF.type, S.TaskProcedure))
    for proc in sorted(procedures, key=lambda x: int(value(g, x, "treeOrder"))):
        tree = ET.SubElement(root, "BehaviorTree", ID=value(g, proc, "treeId"))
        sequence = ET.SubElement(tree, "Sequence")
        for call in sorted(g.objects(proc, S.hasStep), key=lambda x: int(value(g, x, "stepIndex"))):
            node = ET.SubElement(sequence, value(g, call, "xmlTag"))
            skill = g.value(call, S.invokesSkill)
            if node.tag == "SubTree":
                node.set("ID", value(g, skill, "treeId"))
            if g.value(call, S.xmlName) is not None:
                node.set("name", value(g, call, "xmlName"))
            for binding in g.objects(call, S.hasBinding):
                p = g.value(binding, S.bindsParameter)
                sources = [(prop, g.value(binding, prop)) for prop in (S.literalValue, S.sourceParameter, S.sourceData, S.targetData) if g.value(binding, prop) is not None]
                if len(sources) != 1:
                    raise ValueError(f"Ambiguous binding: {binding}")
                prop, item = sources[0]
                if prop == S.literalValue:
                    raw = str(item)
                else:
                    raw = "{" + value(g, item, "parameterName" if prop == S.sourceParameter else "dataKey") + "}"
                node.set(parameter_value(g, p, "parameterName"), raw)
    return root


def node_models_from_graph(g):
    """Build an in-memory OWL port catalog for validation; never export it."""
    root = ET.Element("root")
    catalog = ET.SubElement(root, "TreeNodesModel")
    for skill in sorted(g.subjects(RDF.type, S.AtomicSkill), key=str):
        impl = g.value(skill, S.implementedBy, any=False)
        kind, tag = value(g, impl, "nodeKind"), value(g, impl, "xmlTag")
        if kind not in ("Action", "Condition") or tag == "None":
            raise ValueError(f"原子技能缺少节点契约：{skill}")
        model = ET.SubElement(catalog, kind, ID=tag)
        for contract in sorted(g.objects(impl, S.hasPortContract), key=str):
            direction = value(g, contract, "direction")
            name, cpp_type = value(g, contract, "parameterName"), value(g, contract, "cppType")
            if direction not in ("input", "output") or "None" in (name, cpp_type):
                raise ValueError(f"节点端口契约不完整：{contract}")
            port = ET.SubElement(model, direction + "_port", name=name, type=cpp_type)
            if value(g, contract, "defaultPresent") == "true":
                default = g.value(contract, S.declaredDefault, any=False)
                if default is None:
                    raise ValueError(f"端口缺少已声明的默认值：{contract}")
                port.set("default", str(default))
    for skill in sorted(g.subjects(RDF.type, S.CompositeSkill), key=str):
        model = ET.SubElement(catalog, "SubTree", ID=value(g, skill, "treeId"))
        for relation, direction in ((S.hasInput, "input"), (S.hasOutput, "output")):
            for parameter in sorted(g.objects(skill, relation), key=str):
                contracts = set()
                for binding in g.subjects(S.sourceParameter, parameter):
                    contracts.update(g.objects(parameter_contract(g, g.value(binding, S.bindsParameter)), S.cppType))
                if direction == "output":
                    data = g.value(parameter, S.parameterData)
                    for binding in g.subjects(S.targetData, data):
                        contracts.update(g.objects(parameter_contract(g, g.value(binding, S.bindsParameter)), S.cppType))
                if len(contracts) != 1:
                    raise ValueError(f"复合技能参数缺少唯一端口类型：{parameter}")
                ET.SubElement(model, direction + "_port", name=value(g, parameter, "parameterName"),
                              type=str(next(iter(contracts))))
    return root


def _configuration_bindings(g):
    """Trace public inputs to their literal binding and invocation in OWL."""
    paths = {}
    for skill in g.subjects(RDF.type, S.CompositeSkill):
        fields = paths.setdefault(value(g, skill, "treeId"), {})
        for parameter in g.objects(skill, S.hasInput):
            if value(g, parameter, "isPublicConfig") != "true":
                continue
            calls = list(g.subjects(S.invokesSkill, skill))
            bindings = [(call, binding) for call in calls for binding in g.objects(call, S.hasBinding)
                        if (binding, S.bindsParameter, parameter) in g]
            if len(bindings) != 1:
                raise ValueError(f"公开参数缺少唯一调用绑定：{parameter}")
            call, binding = bindings[0]
            data = g.value(binding, S.sourceData)
            if data is not None:
                producers = [item for item in g.subjects(RDF.type, S.SkillInvocation) if data in produced_data(g, item)]
                if len(producers) != 1 or value(g, producers[0], "xmlTag") != "SetBlackboard":
                    raise ValueError(f"公开参数的数据生产者不明确：{parameter}")
                call = producers[0]
                targets = [item for item in g.objects(call, S.hasBinding)
                           if parameter_value(g, g.value(item, S.bindsParameter), "parameterName") == "value"]
                if len(targets) != 1:
                    raise ValueError(f"赋值节点缺少唯一value绑定：{call}")
                binding = targets[0]
            if any(g.value(binding, prop) is not None for prop in (S.sourceParameter, S.sourceData, S.targetData)):
                raise ValueError(f"配置必须指向本体中的字面量绑定：{binding}")
            parent = g.value(None, S.hasStep, call, any=False)
            steps = sorted(g.objects(parent, S.hasStep), key=lambda item: int(value(g, item, "stepIndex")))
            fields[value(g, parameter, "parameterName")] = {
                "binding": binding, "invocation": call, "tree_id": value(g, parent, "treeId"),
                "step_position": steps.index(call), "port": parameter_value(g, g.value(binding, S.bindsParameter), "parameterName")}

    return paths


def configuration_paths(g):
    return {skill: {name: (item["tree_id"], item["step_position"], item["port"])
                    for name, item in fields.items()}
            for skill, fields in _configuration_bindings(g).items()}
