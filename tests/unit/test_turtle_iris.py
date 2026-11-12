# -*- coding: utf-8 -*-
"""导出 Turtle 时每个对象有自己的 IRI(DR-100)。此前非 ASCII 字符一律替换成下划线,
中文命名的对象全部落到同一个类上。"""
import pytest

import server

rdflib = pytest.importorskip("rdflib")
OWL_CLASS = rdflib.URIRef("http://www.w3.org/2002/07/owl#Class")
RDF_TYPE = rdflib.RDF.type


def _graph(ir):
    g = rdflib.Graph()
    g.parse(data=server._ir_to_turtle("t", ir), format="turtle")
    return g


def _classes(g):
    return {str(s) for s in g.subjects(RDF_TYPE, OWL_CLASS) if str(s).startswith("http://datamind.local/ont#")}


def test_chinese_named_objects_keep_distinct_class_iris():
    ir = {"objects": [{"name": "客户", "table": "t_customer", "kind": "object"}, {"name": "订单", "table": "t_order"},
                      {"name": "部门", "table": "t_dept"}],
          "relations": [{"source_concept": "订单", "target_concept": "客户", "verb": "属于", "status": "verified"}]}
    g = _graph(ir)
    classes = _classes(g)
    assert len(classes) == 3 and any(c.endswith("#客户") for c in classes)
    domains = {str(o) for o in g.objects(None, rdflib.RDFS.domain)}
    ranges = {str(o) for o in g.objects(None, rdflib.RDFS.range)}
    assert any(d.endswith("#订单") for d in domains) and any(r.endswith("#客户") for r in ranges)


def test_names_that_differ_only_in_punctuation_do_not_share_an_iri():
    ir = {"objects": [{"name": "order.item", "table": "a", "kind": "object"}, {"name": "order_item", "table": "b"},
                      {"name": "order-item", "table": "c"}, {"name": "（）", "table": "d"}, {"name": "【】", "table": "e"}],
          "relations": []}
    assert len(_classes(_graph(ir))) == 5


def test_ascii_names_keep_their_previous_iris():
    ir = {"objects": [{"name": "fact_sales_order", "table": "fact_sales_order", "kind": "object"}, {"name": "2023plan", "table": "p"}],
          "relations": []}
    classes = _classes(_graph(ir))
    assert "http://datamind.local/ont#fact_sales_order" in classes and "http://datamind.local/ont#_2023plan" in classes
