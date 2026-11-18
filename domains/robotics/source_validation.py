"""Located checks for the supported XML dialect before RDF extraction — DR-142 / DR-147."""
from copy import deepcopy
import re
import xml.etree.ElementTree as ET

from .profiles.ur_pick_place import COMPOSITES
from .validator import _structure_errors
from .xml_contract import VAR

# 节点/端口/黑板键会成为 RDF IRI 片段，只接受此形式。
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
# 内置 SetBlackboard 契约；节点模型若自行声明，须与之完全一致。
SET_BLACKBOARD_MODEL = ('<Action ID="SetBlackboard"><input_port name="value" type="Any"/>'
                        '<input_port name="output_key" type="std::string"/></Action>')


def builtin_set_blackboard():
    return ET.fromstring(SET_BLACKBOARD_MODEL)


class SourceValidationError(ValueError):
    """Stable issues for API/UI consumers; existing ValueError callers stay compatible."""
    def __init__(self, issues):
        self.issues = deepcopy(issues)
        super().__init__("; ".join(issue["message"] for issue in issues))


def issue(code, message, role, **location):
    return {"code": code, "message": message, "source_role": role,
            "source_asset_id": None, "location": location or {"kind": "unknown"}}


def _fail(code, message, role="behavior_tree", **location):
    raise SourceValidationError([issue(code, message, role, **location)])


def parse_xml(content, *, source_role="behavior_tree"):
    # Strip NULs for UTF-16/32 declarations before the XML parser can resolve anything.
    upper = content.upper().replace(b"\x00", b"")
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        _fail("forbidden_xml_declaration", "DTD and entity declarations are not supported in robotics sources",
              source_role)
    try:
        return ET.fromstring(content)
    except ET.ParseError as exc:
        line, column = exc.position
        _fail("invalid_xml", "XML syntax is invalid", source_role, line=line, column=column)


def _catalog(root, role):
    catalogs = root.findall("TreeNodesModel")
    if len(catalogs) != 1:
        _fail("unsupported_template", "Expected exactly one TreeNodesModel", role)
    nodes = {}
    for node in catalogs[0]:
        name = node.get("ID")
        if not name or name in nodes or node.tag not in ("Action", "Condition", "SubTree"):
            _fail("unsupported_template", "Invalid or duplicate node model", role, model_id=name)
        if not IDENTIFIER.match(name):
            _fail("invalid_identifier", "Node model ID must match [A-Za-z_][A-Za-z0-9_]*", role,
                  element="TreeNodesModel/" + node.tag, attribute="ID", model_id=name)
        ports = {}
        for port in node:
            key = port.get("name")
            if key and not IDENTIFIER.match(key):
                _fail("invalid_identifier", "Port name must match [A-Za-z_][A-Za-z0-9_]*", role,
                      element=port.tag, attribute="name", model_id=name, port=key)
            if (not key or key in ports or (node.tag != "SubTree" and not port.get("type")) or len(port)
                    or port.tag not in ("input_port", "output_port")):
                _fail("invalid_port", "Invalid or duplicate port", role, model_id=name, port=key)
            ports[key] = port
        nodes[name] = (node, ports)
    return nodes


def validate_sources(root, models):
    if (root.tag != "root" or set(root.attrib) != {"BTCPP_format", "main_tree_to_execute"}
            or any(c.tag not in ("BehaviorTree", "TreeNodesModel") for c in root)):
        _fail("unsupported_template", "Unsupported root attributes or elements")
    if models.tag != "root" or models.get("BTCPP_format") != "4":
        _fail("unsupported_template", "Expected BTCPP v4 node models", "node_models")
    if any(c.tag != "TreeNodesModel" for c in models):
        _fail("unsupported_template", "Node-model source contains unsupported elements", "node_models")
    embedded, external = _catalog(root, "behavior_tree"), _catalog(models, "node_models")
    if "SetBlackboard" in external:
        declared, ports = external["SetBlackboard"]
        builtin = builtin_set_blackboard()
        if (declared.tag != builtin.tag or {k: (p.tag, p.get("type")) for k, p in ports.items()}
                != {p.get("name"): (p.tag, p.get("type")) for p in builtin}):
            _fail("model_conflict", "Declared SetBlackboard differs from the built-in contract", "node_models",
                  model_id="SetBlackboard")
    for name in sorted(embedded.keys() & external.keys()):
        node, ports = embedded[name]
        declared, actual = external[name]
        conflicts = sorted(set(ports) ^ set(actual))
        conflicts += [key for key in sorted(ports.keys() & actual.keys())
                      if (ports[key].tag, ports[key].get("type")) != (actual[key].tag, actual[key].get("type"))]
        if node.tag != declared.tag or conflicts:
            location = {"model_id": name}
            if conflicts:
                location["port"] = conflicts[0]
            raise SourceValidationError([issue("model_conflict", "Conflicting node model", role, **location)
                                         for role in ("behavior_tree", "node_models")])
    for name in sorted(embedded.keys() - external.keys()):
        node, ports = embedded[name]
        if name not in COMPOSITES or node.tag != "SubTree":
            _fail("unknown_node", "Unknown embedded node model", model_id=name)
        _, inputs, outputs = COMPOSITES[name]
        expected = {k: "input_port" for k in inputs} | {k: "output_port" for k in outputs}
        if {key: port.tag for key, port in ports.items()} != expected:
            _fail("invalid_port", "Unsupported subtree interface", model_id=name)
    # Inspect actual elements for unknown calls before the fixed-template comparison.
    # This supplies concrete positions rather than parsing the validator's prose.
    for tree in root.findall("BehaviorTree"):
        tree_id = tree.get("ID")
        if len(tree) != 1 or tree[0].tag != "Sequence":
            _fail("unsupported_template", "This profile supports Sequence procedures only", tree_id=tree_id)
        if set(tree.attrib) != {"ID"} or tree[0].attrib:
            _fail("unsupported_template", "Unsupported tree or sequence attributes", tree_id=tree_id)
        for index, node in enumerate(tree[0], 1):
            location = {"tree_id": tree_id, "node_tag": node.tag, "step_index": index}
            outputs = set()
            if node.tag == "SetBlackboard":
                allowed, required = {"value", "output_key", "name"}, {"value", "output_key"}
            elif node.tag == "SubTree":
                if node.get("ID") not in COMPOSITES:
                    _fail("unknown_node", "Unknown subtree", **location)
                _, inputs, outputs = COMPOSITES[node.get("ID")]
                allowed = set(inputs + outputs) | {"name", "ID"}
                required = set(inputs + outputs) | {"ID"}
            else:
                if node.tag not in external:
                    _fail("unknown_node", "Missing node model", **location)
                _, ports = external[node.tag]
                allowed = set(ports) | {"name"}
                required = {name for name, port in ports.items()
                            if port.tag == "input_port" and not port.get("default", "").strip()}
                outputs = {name for name, port in ports.items() if port.tag == "output_port"}
            wrong = sorted((required - set(node.attrib)) | (set(node.attrib) - allowed))
            if wrong:
                _fail("invalid_port", "Missing or unknown ports", **location, port=wrong[0])
            for key in sorted(set(outputs) & set(node.attrib)):
                if not VAR.fullmatch(node.get(key)):
                    _fail("invalid_binding", "Output ports must bind a blackboard variable such as {name}",
                          **location, attribute=key, port=key)
            if node.tag == "SetBlackboard" and not IDENTIFIER.match(node.get("output_key")):
                _fail("invalid_identifier", "SetBlackboard output_key must match [A-Za-z_][A-Za-z0-9_]*",
                      **location, attribute="output_key", port="output_key")
    errors = _structure_errors(root)
    if errors:
        # Keep the validator's explicit path/code; do not infer line numbers from text.
        raise SourceValidationError([issue("unsupported_template", error["message"], "behavior_tree",
                                           xml_path=error["path"], rule=error["code"]) for error in errors])
