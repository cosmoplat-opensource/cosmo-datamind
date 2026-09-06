# -*- coding: utf-8 -*-
"""指标契约路由(DR-054):契约查看、人工确认口径的边界、重跑核验。"""
import json

import server
import bp_metrics


CONTRACT = {"id": "m1", "name": "销售金额", "layer": "atomic", "table": "fact_sales_order", "entity": "order",
            "measure": {"col": "amount", "agg": "sum"}, "filters": [], "time": {"col": "order_date", "grain": ["month"]},
            "dimensions": [], "status": "candidate", "value_col": "amount"}
LEGACY = {"id": "old", "name": "计划产量", "table": "T", "value_col": "p", "candidate": True}


def _ir():
    return {"objects": [{"id": "order", "table": "fact_sales_order", "attrs": [{"col": "amount"}, {"col": "status"}]},
                        {"id": "customer", "table": "dim_customer", "attrs": [{"col": "region"}]}],
            "links": [{"source": "order", "target": "customer", "status": "verified",
                       "evidence": {"child_key": "cust_id", "parent_key": "cust_id"}}],
            "metric_layers": {"atomic": [dict(CONTRACT), dict(LEGACY)]}}


def _wire(monkeypatch, tmp_path, ir):
    wp = tmp_path / "g.json"
    writes = []
    bp_metrics.configure_metrics(
        load_graph=lambda k: (None if k == "../x" else (ir if k == "g" else {})),
        open_writable=lambda k: (ir, str(wp), None) if k == "g" else (None, None, (server.jsonify({"error": "图谱不存在"}), 404)),
        db_for_source=lambda s: None,
        write_json=lambda path, data: writes.append((path, json.loads(json.dumps(data)))),
        write_lock=server._WRITE_LOCK)
    return writes


def test_contract_view_compiles_and_lists_dimensions(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path, _ir())
    r = server.app.test_client().get("/api/metric/contract?graph=g&name=销售金额")
    body = r.get_json()
    assert r.status_code == 200 and body["is_contract"]
    assert body["compiled_sql"].startswith('SELECT SUM("amount")')
    assert body["allowed_dimensions"]["objects"][0]["table"] == "dim_customer"
    assert server.app.test_client().get("/api/metric/contract?graph=../x&name=a").status_code == 400


def test_status_rejects_verified_and_requires_reviewer_for_certified(monkeypatch, tmp_path):
    writes = _wire(monkeypatch, tmp_path, _ir())
    c = server.app.test_client()
    assert c.post("/api/metric/status", json={"graph": "g", "metric": "销售金额", "status": "verified"}).status_code == 400
    assert c.post("/api/metric/status", json={"graph": "g", "metric": "销售金额", "status": "certified"}).status_code == 400
    r = c.post("/api/metric/status", json={"graph": "g", "metric": "销售金额", "status": "certified",
                                            "reviewer": "张三", "reason": "财务口径确认"})
    assert r.status_code == 200 and r.get_json()["status"] == "certified"
    saved = writes[-1][1]["metric_layers"]["atomic"][0]
    assert saved["certified_by"] == "张三" and saved["candidate"] is False
    assert not writes[-1][0].endswith("demo_ir.json")


def test_legacy_metric_cannot_be_certified(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path, _ir())
    r = server.app.test_client().post("/api/metric/status", json={"graph": "g", "metric": "计划产量",
                                                                  "status": "certified", "reviewer": "张三"})
    assert r.status_code == 400 and "契约" in r.get_json()["error"]


def test_adjudicate_requires_usable_source(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path, _ir())
    r = server.app.test_client().post("/api/metric/adjudicate", json={"graph": "g", "source": "ext"})
    assert r.status_code == 400


def test_metric_cards_prefer_certified_and_expose_caliber():
    ir = _ir()
    ir["metric_layers"]["atomic"][0]["status"] = "certified"
    cards = server._metric_cards("本月销售金额和计划产量", ir)
    assert [c["name"] for c in cards] == ["销售金额", "计划产量"]
    assert cards[0]["caliber"].startswith("SUM(amount)") and cards[0]["status"] == "certified"
    assert cards[1]["status"] == "candidate" and "caliber" not in cards[1]
