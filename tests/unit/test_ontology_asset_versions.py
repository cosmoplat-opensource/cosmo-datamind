"""T02 RED: immutable payloads, RDF identity and publication gate — DR-059 / DR-060 / DR-065."""
from copy import deepcopy
import hashlib

import pytest
from rdflib import BNode, Graph, Literal, RDF, RDFS, URIRef, XSD
from rdflib.collection import Collection
from rdflib.compare import isomorphic

from tests.asset_support import add_data, add_robotics, data_bytes, store_at
from tests.robotics_generation_support import assert_no_candidate, task
from tests.robotics_support import S, api


def test_versions_keep_old_bytes_and_snapshot_metadata(tmp_path):
    store = store_at(tmp_path / "assets")
    asset, first = add_data(store)
    aid, v1 = asset["ontology_id"], first["version_id"]
    metadata = {"legacy_key": "built_parts", "edit_digest": "a" * 64}
    second = store.add_version(aid, data_bytes("已审核零件"), payload_kind="datamind_ir",
                               parent_version_id=v1, metadata=metadata)
    metadata["edit_digest"] = "changed-after-call"
    assert second["version_id"] != v1 and second["parent_version_id"] == v1
    assert store.read_payload(aid, v1) == data_bytes()
    assert store.read_payload(aid, second["version_id"]) == data_bytes("已审核零件")
    assert store.get_version(aid, v1)["payload_sha256"] == hashlib.sha256(data_bytes()).hexdigest()
    saved = store.get_version(aid, second["version_id"])
    assert saved["metadata"]["edit_digest"] == "a" * 64
    saved["metadata"]["edit_digest"] = "mutated-return-value"
    assert store.get_version(aid, second["version_id"])["metadata"]["edit_digest"] == "a" * 64
    assert len(store.list_versions(aid)) == 2


@pytest.mark.parametrize("parent", ["missing", "other_asset"])
def test_invalid_parent_does_not_create_a_version(tmp_path, parent):
    store = store_at(tmp_path / "assets")
    asset, _ = add_data(store)
    _, other = add_data(store, name="another")
    aid = asset["ontology_id"]
    before = deepcopy(store.list_versions(aid))
    with pytest.raises((KeyError, ValueError)):
        store.add_version(aid, data_bytes("新值"), payload_kind="datamind_ir",
                          parent_version_id=other["version_id"] if parent == "other_asset" else "missing")
    assert store.list_versions(aid) == before


@pytest.mark.parametrize("kind,payload", [
    ("robotics_rdf", b"<rdf/>"), ("unknown", b"{}"), ("datamind_ir", b"not-json"),
    ("datamind_ir", b"[]"), ("datamind_ir", b'{"objects":"invalid"}'),
])
def test_invalid_payload_or_domain_kind_mismatch_is_not_indexed(tmp_path, kind, payload):
    store = store_at(tmp_path / "assets")
    asset, version = add_data(store)
    aid = asset["ontology_id"]
    before = deepcopy(store.list_versions(aid))
    with pytest.raises(ValueError):
        store.add_version(aid, payload, payload_kind=kind)
    assert store.list_versions(aid) == before
    assert store.read_payload(aid, version["version_id"]) == data_bytes()


def test_unknown_source_or_asset_cannot_create_version(tmp_path):
    store = store_at(tmp_path / "assets")
    asset, _ = add_data(store)
    aid = asset["ontology_id"]
    before = deepcopy(store.list_versions(aid))
    with pytest.raises((KeyError, ValueError)):
        store.add_version(aid, data_bytes(), payload_kind="datamind_ir", source_asset_ids=["missing"])
    with pytest.raises(KeyError):
        store.add_version("missing", data_bytes(), payload_kind="datamind_ir")
    assert store.list_versions(aid) == before


def test_fresh_owl_sources_and_consumer_validation_are_persisted(tmp_path):
    store = store_at(tmp_path / "assets")
    asset, version, content, sources = add_robotics(store, tmp_path)
    aid, vid = asset["ontology_id"], version["version_id"]
    saved = store.get_version(aid, vid)
    assert saved["publication_state"] == "candidate"
    assert saved["source_asset_ids"] == [s["source_asset_id"] for s in sources]
    assert saved["metadata"]["profile_id"] == "ur_pick_place"
    assert saved["metadata"]["build_recipe"] == "bt_xml"
    assert store.read_payload(aid, vid) == content
    with pytest.raises(ValueError):
        store.load_robotics(aid, vid, mode="real")
    store.publish_version(aid, vid, mode="real")
    saved = store_at(tmp_path / "assets").get_version(aid, vid)
    assert saved["publication_state"] == "published"
    assert saved["validation"]["shacl_conforms"] is True
    assert saved["validation"]["ontology_sha256"] == hashlib.sha256(content).hexdigest()
    assert saved["validation"]["execution_readiness"] == "not_verified"
    context = store.load_robotics(aid, vid, mode="real")
    assert isomorphic(context["graph"], Graph().parse(data=content, format="xml"))
    assert store.read_payload(aid, vid) == content


@pytest.mark.parametrize("change", ["contract", "binding", "mode"])
def test_publication_revalidates_actual_semantics_not_claimed_success(tmp_path, change):
    store = store_at(tmp_path / "assets")
    asset, version, content, _ = add_robotics(store, tmp_path)
    graph = Graph().parse(data=content, format="xml")
    ontology = URIRef("https://example.org/ur-skill")
    if change == "contract":
        graph.set((ontology, S.artifactContract, Literal("unknown-v99", datatype=XSD.string)))
    elif change == "binding":
        graph.set((S.grasp_3__target, S.bindsParameter, S.MoveJ_Skill__target))
    else:
        graph.set((ontology, S.datasetKind, Literal("demo", datatype=XSD.string)))
    bad = graph.serialize(format="xml", encoding="utf-8")
    aid = asset["ontology_id"]
    candidate = store.add_version(aid, bad, payload_kind="robotics_rdf",
                                  parent_version_id=version["version_id"],
                                  metadata={"validation": {"shacl_conforms": True}})
    with pytest.raises(ValueError):
        store.publish_version(aid, candidate["version_id"], mode="real")
    assert store.get_version(aid, candidate["version_id"])["publication_state"] == "candidate"
    with pytest.raises(ValueError):
        store.load_robotics(aid, candidate["version_id"], mode="real")
    assert store.read_payload(aid, version["version_id"]) == content


def test_rdf_terms_and_same_iri_remain_isolated_by_asset_and_version(tmp_path):
    store = store_at(tmp_path / "assets")
    subject = URIRef("https://example.org/shared-part")
    payloads = []
    for name in ("工位甲", "工位乙"):
        graph = Graph()
        graph.add((subject, RDFS.label, Literal(name, lang="zh")))
        graph.add((subject, URIRef("urn:count"), Literal("2", datatype=XSD.integer)))
        head = BNode()
        graph.add((subject, URIRef("urn:steps"), head))
        Collection(graph, head, [URIRef("urn:step1"), URIRef("urn:step2")])
        payloads.append(graph.serialize(format="xml", encoding="utf-8"))
    a = store.create_asset(name="A", domain="robotics")
    b = store.create_asset(name="B", domain="robotics")
    v1 = store.add_version(a["ontology_id"], payloads[0], payload_kind="robotics_rdf")
    v2 = store.add_version(a["ontology_id"], payloads[1], payload_kind="robotics_rdf",
                           parent_version_id=v1["version_id"])
    vb = store.add_version(b["ontology_id"], payloads[1], payload_kind="robotics_rdf")
    for asset, version, payload in ((a, v1, payloads[0]), (a, v2, payloads[1]), (b, vb, payloads[1])):
        raw = store.read_payload(asset["ontology_id"], version["version_id"])
        assert raw == payload
        recovered = Graph().parse(data=raw, format="xml")
        assert isomorphic(recovered, Graph().parse(data=payload, format="xml"))
        assert list(Collection(recovered, recovered.value(subject, URIRef("urn:steps")))) == [
            URIRef("urn:step1"), URIRef("urn:step2")]
        assert (None, RDF.first, URIRef("urn:step1")) in recovered
    with pytest.raises(KeyError):
        store.read_payload(b["ontology_id"], v1["version_id"])


def test_old_published_snapshot_loads_after_new_version_is_registered(tmp_path):
    store = store_at(tmp_path / "assets")
    asset, version, content, _ = add_robotics(store, tmp_path)
    aid, vid = asset["ontology_id"], version["version_id"]
    store.publish_version(aid, vid, mode="real")
    graph = Graph().parse(data=content, format="xml")
    graph.add((S.PartType_A, RDFS.comment, Literal("new revision")))
    newer = store.add_version(aid, graph.serialize(format="xml", encoding="utf-8"),
                              payload_kind="robotics_rdf", parent_version_id=vid)
    store.publish_version(aid, newer["version_id"], mode="real")
    context = store_at(tmp_path / "assets").load_robotics(aid, vid, mode="real")
    assert context["validation"]["ontology_sha256"] == hashlib.sha256(content).hexdigest()
    assert not list(context["graph"].objects(S.PartType_A, RDFS.comment))
    # Loading a contract-complete version must not manufacture missing business evidence.
    result = api("generation").generate_task(context, task(), mode="real", output_dir=tmp_path / "result")
    assert_no_candidate(result, tmp_path / "result", "needs_information")
