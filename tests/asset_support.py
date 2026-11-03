"""Isolated asset inputs and proposed service contract — DR-059 / DR-064 / DR-065."""
import importlib
import json

from tests.robotics_support import MODELS, SOURCE, api, build


def store_at(root):
    # Missing implementation is an individual test failure, never a collection error.
    return importlib.import_module("ontology_assets").AssetStore(root)


def data_ir(label="零件"):
    return {"scenario": {"name": "装配数据"}, "objects": [
        {"id": "part", "name": "parts", "cn": label, "table": "parts"}], "relations": []}


def data_bytes(label="零件"):
    return json.dumps(data_ir(label), ensure_ascii=False).encode()


def add_data(store, *, name="数据本体", payload=None):
    asset = store.create_asset(name=name, domain="data")
    version = store.add_version(asset["ontology_id"], data_bytes() if payload is None else payload,
                                payload_kind="datamind_ir")
    return asset, version


def fresh_owl(directory):
    path = directory / "fresh.owl"
    api("publisher").publish_ontology(build(), path, mode="real")
    return path.read_bytes()


def add_robotics(store, directory):
    sources = [store.put_source(path.read_bytes(), role=role, media_type="application/xml")
               for path, role in ((SOURCE, "behavior_tree"), (MODELS, "node_models"))]
    content = fresh_owl(directory)
    asset = store.create_asset(name="机器人本体", domain="robotics")
    version = store.add_version(asset["ontology_id"], content, payload_kind="robotics_rdf",
                                source_asset_ids=[s["source_asset_id"] for s in sources],
                                metadata={"profile_id": "ur_pick_place", "build_recipe": "bt_xml"})
    return asset, version, content, sources


def legacy_api():
    return importlib.import_module("ontology_assets.legacy").register_legacy
