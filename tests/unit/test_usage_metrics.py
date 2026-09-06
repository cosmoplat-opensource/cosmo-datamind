# -*- coding: utf-8 -*-
"""指标使用度(DR-054):高频 × candidate 指标进入建模优先级建议。"""
import usage_stat


def test_metric_usage_reported_and_high_freq_candidate_advised(tmp_path):
    ir = {"objects": [{"id": "o", "cn": "订单"}], "links": [],
          "metric_layers": {"atomic": [{"name": "销售金额", "status": "candidate"},
                                       {"name": "订单数", "status": "certified"}]}}
    usage_stat.record(str(tmp_path), "g", ["o", "metric:销售金额", "metric:订单数"], "query")
    usage_stat.record(str(tmp_path), "g", ["metric:销售金额"], "query")
    rep = usage_stat.report(str(tmp_path), ir, "g")
    assert rep["metrics"][0] == {"name": "销售金额", "calls": 2, "status": "candidate", "last": rep["metrics"][0]["last"]}
    hits = [a for a in rep["advice"] if a.get("metric") == "销售金额"]
    assert hits and hits[0]["priority"] == "high"
    assert not [a for a in rep["advice"] if a.get("metric") == "订单数"]
