"""Validate the supported XML dialect before RDF extraction — DR-060."""
import xml.etree.ElementTree as ET

from .profiles.ur_pick_place import COMPOSITES
from .validator import _structure_errors


def parse_xml(content):
    # Domain source documents cannot define external dependencies or entities.
    upper = content.upper().replace(b"\x00", b"")
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise ValueError("DTD and entity declarations are not supported in robotics sources")
    return ET.fromstring(content)


def _catalog(root):
    catalogs = root.findall("TreeNodesModel")
    if len(catalogs) != 1:
        raise ValueError("Expected exactly one TreeNodesModel")
    nodes = {}
    for node in catalogs[0]:
        name = node.get("ID")
        if not name or name in nodes or node.tag not in ("Action", "Condition", "SubTree"):
            raise ValueError(f"Invalid or duplicate node model: {name}")
        ports = {}
        for port in node:
            key = port.get("name")
            # Source SubTree interfaces omit C++ types; semantics derives them
            # from the unique typed atomic-port bindings inside that subtree.
            if (not key or key in ports or (node.tag != "SubTree" and not port.get("type")) or len(port)
                    or port.tag not in ("input_port", "output_port")):
                raise ValueError(f"Invalid or duplicate port: {name}.{key}")
            ports[key] = port
        nodes[name] = (node, ports)
    return nodes


def validate_sources(root, models):
    errors = _structure_errors(root)
    if errors:
        raise ValueError(errors)
    if (set(root.attrib) != {"BTCPP_format", "main_tree_to_execute"}
            or any(c.tag not in ("BehaviorTree", "TreeNodesModel") for c in root)):
        raise ValueError("Unsupported root attributes or elements")
    if models.tag != "root" or models.get("BTCPP_format") != "4":
        raise ValueError("Expected BTCPP v4 node models")
    if any(c.tag != "TreeNodesModel" for c in models):
        raise ValueError("Node-model source contains unsupported elements")
    embedded, external = _catalog(root), _catalog(models)
    for name in embedded.keys() & external.keys():
        node, ports = embedded[name]
        declared, actual = external[name]
        if node.tag != declared.tag or set(ports) != set(actual) or any(
            (port.tag, port.get("type")) != (actual[key].tag, actual[key].get("type"))
            for key, port in ports.items()
        ):
            raise ValueError(f"Conflicting node model: {name}")
    for name in embedded.keys() - external.keys():
        node, ports = embedded[name]
        if name not in COMPOSITES or node.tag != "SubTree":
            raise ValueError(f"Unknown embedded node model: {name}")
        _, inputs, outputs = COMPOSITES[name]
        expected = {k: "input_port" for k in inputs} | {k: "output_port" for k in outputs}
        if {key: port.tag for key, port in ports.items()} != expected:
            raise ValueError(f"Unsupported subtree interface: {name}")
    for tree in root.findall("BehaviorTree"):
        if set(tree.attrib) != {"ID"} or tree[0].attrib:
            raise ValueError(f"Unsupported tree or sequence attributes: {tree.get('ID')}")
        for index, node in enumerate(tree[0], 1):
            path = f"{tree.get('ID')}/{index}:{node.tag}"
            if node.tag == "SetBlackboard":
                allowed = {"value", "output_key", "name"}
                required = {"value", "output_key"}
            elif node.tag == "SubTree":
                _, inputs, outputs = COMPOSITES[node.get("ID")]
                allowed = set(inputs + outputs) | {"name", "ID"}
                required = set(inputs + outputs) | {"ID"}
            else:
                if node.tag not in external:
                    raise ValueError(f"Missing node model: {path}")
                _, ports = external[node.tag]
                allowed = set(ports) | {"name"}
                required = {name for name, port in ports.items()
                            if port.tag == "input_port" and not port.get("default", "").strip()}
            if not required <= set(node.attrib) or set(node.attrib) - allowed:
                raise ValueError(f"Missing or unknown ports: {path}")
