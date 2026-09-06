# -*- coding: utf-8 -*-
"""概念画像(DR-055):确定性聚合与可回放检索。"""
import concept_profile as CP

IR = {
    "objects": [
        {"id": "order", "name": "fact_sales_order", "cn": "销售订单", "table": "fact_sales_order", "kind": "event",
         "definition": "记录客户购买承诺的业务单据", "aliases": ["订单"],
         "attrs": [{"col": "amount", "cn": "金额"}, {"col": "cust_id", "cn": "客户编号"}]},
        {"id": "customer", "name": "dim_customer", "cn": "客户", "table": "dim_customer", "kind": "object",
         "attrs": [{"col": "region", "cn": "区域"}]},
        {"id": "island", "name": "audit_log", "cn": "审计日志", "table": "audit_log"},
    ],
    "links": [{"source": "order", "target": "customer", "verb": "归属", "status": "verified"},
              {"source": "island", "target": "customer", "verb": "记录", "status": "candidate"}],
    "metric_layers": {"atomic": [{"name": "销售金额", "table": "fact_sales_order", "status": "certified",
                                  "measure": {"col": "amount", "agg": "sum"}, "value_col": "amount"}]},
}


def test_build_is_deterministic_and_only_uses_strong_relations():
    a, b = CP.build(IR), CP.build(IR)
    assert a == b
    order = next(p for p in a if p["key"] == "order")
    assert order["relations"] == [{"object": "customer", "cn": "客户", "verb": "归属", "direction": "out", "status": "verified"}]
    customer = next(p for p in a if p["key"] == "customer")
    assert [r["object"] for r in customer["relations"]] == ["order"]      # candidate 边不进画像
    assert order["metrics"][0]["caliber"].startswith("SUM(amount)")


def test_search_ranks_name_hits_above_column_hits_and_is_stable():
    profiles = CP.build(IR)
    hits = CP.search(profiles, "各区域的订单金额是多少")
    assert [h["key"] for h in hits] == ["order", "customer"]
    assert hits[0]["score"] > hits[1]["score"] and "订单" in hits[0]["hits"]
    assert CP.search(profiles, "") == []


def test_render_contains_relations_and_metrics():
    text = CP.render(CP.build(IR)[2])
    assert "销售订单 归属 客户[verified]" in text and "销售金额[certified]" in text
