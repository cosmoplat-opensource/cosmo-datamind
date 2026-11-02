"""Frozen inputs and independent XML assertions — DR-060 / DR-065."""
import importlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from rdflib import Namespace

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "robotics"
SOURCE = FIXTURES / "sources" / "xingweishu.xml"
MODELS = FIXTURES / "sources" / "xml_models.xml"
S = Namespace("https://example.org/ur-skill#")


def api(name):
    # Imports happen inside tests so an absent implementation is a test failure,
    # not a collection error that prevents review of the other cases.
    return importlib.import_module("domains.robotics." + name)


def build():
    return api("importer").build_graph(SOURCE, MODELS, profile="ur_pick_place")


def signature(node):
    return node.tag, tuple(sorted(node.attrib.items())), tuple(signature(child) for child in node)


def trees(root):
    return tuple(signature(tree) for tree in root.findall("BehaviorTree"))


def prepared():
    graph = build()
    semantics = api("semantics")
    return (
        semantics.xml_from_graph(graph),
        semantics.node_models_from_graph(graph),
        semantics.configuration_paths(graph),
    )


def config():
    return json.loads((FIXTURES / "tasks" / "public_parameters.json").read_text())


def sequence(root, name):
    result = root.find(f"BehaviorTree[@ID='{name}']/Sequence")
    assert result is not None, name
    return result


def source_root():
    return ET.parse(SOURCE).getroot()
