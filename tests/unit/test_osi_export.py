# -*- coding: utf-8 -*-
"""Apache Ossie 导出(DR-055):形状对齐官方 schema、状态只走扩展、确定性发射。"""
import copy
import json

import pytest

import osi_export as OE
from tests.unit.test_concept_profile import IR as _BASE

IR: dict = copy.deepcopy(_BASE)
IR["links"][0]["evidence"] = {"child_key": "cust_id", "parent_key": "cust_id", "overlap": 100.0, "parent_unique": True}
IR["objects"][0]["bfo"] = "Process"
IR["objects"][0]["pk"] = "order_id"
IR["objects"][0]["attrs"].append({"col": "order_date", "cn": "下单日期", "type": "DATE"})
IR["metric_layers"]["atomic"][0].update({"filters": [{"col": "status", "op": "!=", "value": "cancelled"}],
                                         "time": {"col": "order_date", "grain": ["month"]},
                                         "evidence": {"compiled_sql": "SELECT 1", "executed": True, "match": True,
                                                      "reference": {"source": "gold:Q1", "match": True}}})
IR["metric_layers"]["atomic"].append({"name": "计划产量", "table": "t", "value_col": "p", "candidate": True})


def _ext(obj):
    return json.loads(obj["custom_extensions"][0]["data"])


def test_semantic_model_shape_matches_spec_and_hides_status_in_extensions():
    doc = OE.semantic_model(IR, "g")
    assert doc["version"] == "0.2.0.dev0" and isinstance(doc["semantic_model"], list)
    m = doc["semantic_model"][0]
    assert set(m) <= {"name", "description", "ai_context", "datasets", "relationships", "metrics", "custom_extensions"}
    ds = {d["name"]: d for d in m["datasets"]}
    assert ds["fact_sales_order"]["primary_key"] == ["order_id"]
    date_field = next(f for f in ds["fact_sales_order"]["fields"] if f["name"] == "order_date")
    assert date_field["datatype"] == "Date" and date_field["dimension"] == {"is_time": True}
    assert "status" not in m["relationships"][0]
    r = m["relationships"][0]
    assert r["from"] == "fact_sales_order" and r["to"] == "dim_customer" and r["from_columns"] == ["cust_id"]
    assert _ext(r)["status"] == "verified"
    # candidate 关系不成为 JOIN 路径,但不丢失
    assert _ext(m)["candidate_relations"][0]["source"] == "island"
    met = {x["name"]: x for x in m["metrics"]}
    assert met["销售金额"]["expression"]["dialects"][0]["expression"] == \
        "SUM(CASE WHEN fact_sales_order.status != 'cancelled' THEN fact_sales_order.amount END)"
    assert met["销售金额"]["datatype"] == "Decimal" and _ext(met["销售金额"])["status"] == "certified"
    assert "计划产量" not in met and _ext(m)["unbound_metrics"][0]["name"] == "计划产量"


def _walk(v, path="$"):
    if isinstance(v, dict):
        for k, x in v.items():
            assert x not in (None, "", [], {}), f"{path}.{k} 为空值,schema 不接受"
            _walk(x, f"{path}.{k}")
    elif isinstance(v, list):
        for i, x in enumerate(v):
            _walk(x, f"{path}[{i}]")


def test_no_null_or_empty_values_and_deterministic():
    for kind in ("semantic_model", "ontology"):
        doc = OE.ontology(IR, "g") if kind == "ontology" else OE.semantic_model(IR, "g")
        _walk(doc)
        assert OE.to_yaml(IR, "g", kind) == OE.to_yaml(IR, "g", kind)


def test_ontology_document_uses_upper_categories_and_verbalizes():
    doc = OE.ontology(IR, "g")
    names = [c["concept"] for c in doc["ontology"]]
    assert names[0] == "Process" and "order" in names
    order = next(c for c in doc["ontology"] if c["concept"] == "order")
    assert order["extends"] == ["Process"]
    rel = order["relationships"][0]
    assert rel["verbalizes"] == ["{order} 归属 {customer}"] and rel["multiplicity"] == "ManyToOne"
    assert rel["roles"] == [{"concept": "customer"}]
    island = next(c for c in doc["ontology"] if c["concept"] == "island")
    assert "relationships" not in island                       # candidate 边不进本体


def test_emitter_quotes_and_escapes():
    text = OE.emit({"a": 'x"y', "b": [1, "z\nw", {"c": True}]})
    assert 'a: "x\\"y"' in text and '- "z\\nw"' in text and "- c: true" in text


@pytest.mark.parametrize("kind", ["semantic_model", "ontology"])
def test_export_validates_against_snapshotted_official_schema(kind):
    """有 jsonschema 时按快照的官方 schema 校验;没有则跳过而不是假装通过。"""
    pytest.importorskip("jsonschema")
    doc = OE.ontology(IR, "g") if kind == "ontology" else OE.semantic_model(IR, "g")
    errors = OE.validate(doc, kind)
    assert errors == [], errors[:5]


def test_yaml_round_trips_through_pyyaml_when_available():
    yaml = pytest.importorskip("yaml")
    doc = OE.semantic_model(IR, "g")
    assert yaml.safe_load(OE.emit(doc) + "\n") == doc
