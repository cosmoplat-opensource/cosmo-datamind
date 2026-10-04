"""精确作答(answer_style=exact):只回答问题本身,便于与标准答案逐行比对(BIRD 等基准)。

本体上下文、本体约束校验、按原因修正与意图核对照常进行,只改变作答形式:
只取一个分析;SELECT 只含问题要求的列;不额外加分组列、ROUND 与 LIMIT。"""
from __future__ import annotations

import server


def _capture_prompt(monkeypatch):
    seen = {}

    class _RT:
        pass

    def fake_turn(rt, sid, prompt, timeout, task=None):
        seen["prompt"] = prompt
        return True, '{"analyses":[{"title":"a","sql":"SELECT 1"}],"note":""}'

    import agent_runtime
    monkeypatch.setattr(agent_runtime, "available", lambda: ["openai"])
    monkeypatch.setattr(agent_runtime, "get_runtime", lambda d: _RT())
    monkeypatch.setattr(server, "_drv_order", lambda: ["openai"])
    monkeypatch.setattr(server, "_llm_turn", fake_turn)
    return seen


def test_exact_style_prompt_asks_for_one_answer_with_requested_columns_only(monkeypatch):
    seen = _capture_prompt(monkeypatch)
    token = server._ANSWER_STYLE.set("exact")
    try:
        server.agent_sql_plan("PA66-GF30 的平均成交单价是多少?", "表 t(...)", [])
    finally:
        server._ANSWER_STYLE.reset(token)
    p = seen["prompt"]
    assert "只输出 1 个分析" in p and "只包含问题要求返回的列" in p
    assert "最多3个分析" not in p


def test_date_format_is_taken_from_column_values_not_assumed(monkeypatch):
    seen = _capture_prompt(monkeypatch)
    server.agent_sql_plan("每月销量", "表 t(...)", [])
    assert "'YYYY-MM-DD'" not in seen["prompt"]            # 不同库的日期格式不同(如 YYYYMM),不预设
    assert "以列说明中的实际取值为准" in seen["prompt"]


def test_exact_style_keeps_only_the_first_analysis(monkeypatch):
    plans = {"analyses": [{"title": "答", "sql": "SELECT COUNT(*) AS n FROM fact_sales_order"},
                          {"title": "拆分", "sql": "SELECT substr(order_date,1,7) m, COUNT(*) FROM fact_sales_order GROUP BY 1"}]}
    monkeypatch.setattr(server, "agent_sql_plan", lambda *a, **k: plans)
    monkeypatch.setattr(server, "narrative_llm", lambda *a, **k: "ok")
    c = server.app.test_client()
    full = c.post("/api/chat", json={"q": "销售订单有多少条", "nocache": 1}).get_json()
    exact = c.post("/api/chat", json={"q": "销售订单有多少条", "nocache": 1, "answer_style": "exact"}).get_json()
    assert len(full["results"]) == 2
    assert len(exact["results"]) == 1 and exact["results"][0]["title"] == "答"


def test_exact_style_prompt_has_no_report_only_instructions(monkeypatch):
    """报告式的「LIMIT 500 以内」「按月 substr」与精确作答冲突,只放在报告式规则里。"""
    seen = _capture_prompt(monkeypatch)
    token = server._ANSWER_STYLE.set("exact")
    try:
        server.agent_sql_plan("q", "ctx", [])
    finally:
        server._ANSWER_STYLE.reset(token)
    assert "LIMIT 500" not in seen["prompt"] and "substr(日期列,1,7)" not in seen["prompt"]
    seen2 = _capture_prompt(monkeypatch)
    server.agent_sql_plan("q", "ctx", [])
    assert "LIMIT 500 以内" in seen2["prompt"]
