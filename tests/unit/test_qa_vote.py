"""精确作答的多路候选与执行结果投票(DR-081)。

同一问题按几种规划策略各生成一条 SQL,各自经本体约束校验、修正与按结果核对后执行;
按结果行集合分组,取票数最多的一组;票数相同时由模型在各组中选一条。"""
from __future__ import annotations

import server


def test_vote_picks_largest_result_group():
    cands = [{"rows": [{"n": 3}], "sql": "a"}, {"rows": [{"n": 3}], "sql": "b"}, {"rows": [{"n": 4}], "sql": "c"}]
    i, info = server._vote_candidates("q", cands, chooser=lambda q, groups: 0)
    assert cands[i]["sql"] in ("a", "b") and info["groups"] == 2 and info["votes"] == 2


def test_vote_treats_float_noise_and_row_order_as_equal():
    cands = [{"rows": [{"x": 0.1 + 0.2}, {"x": 1}], "sql": "a"}, {"rows": [{"y": 1}, {"y": 0.3}], "sql": "b"},
             {"rows": [{"x": 9}], "sql": "c"}]
    i, info = server._vote_candidates("q", cands, chooser=lambda q, groups: 1)
    assert info["votes"] == 2 and cands[i]["sql"] in ("a", "b")


def test_vote_tie_is_broken_by_chooser():
    cands = [{"rows": [{"n": 1}], "sql": "a"}, {"rows": [{"n": 2}], "sql": "b"}]
    i, info = server._vote_candidates("q", cands, chooser=lambda q, groups: 1)
    assert cands[i]["sql"] == "b" and info["tie"] is True


def test_exact_chat_with_candidates_returns_majority(monkeypatch):
    sqls = {"": "SELECT COUNT(*) AS n FROM fact_sales_order",
            "probe": "SELECT COUNT(*) AS n FROM fact_sales_order",
            "decompose": "SELECT COUNT(*) AS n FROM dim_customer"}
    monkeypatch.setattr(server, "agent_sql_plan",
                        lambda *a, **k: {"analyses": [{"title": "答", "sql": sqls[server._PLAN_VARIANT.get()]}]})
    monkeypatch.setattr(server, "_verify_answer", lambda *a, **k: None)
    r = server.app.test_client().post("/api/chat", json={"q": "销售订单有多少条", "nocache": 1,
                                                         "answer_style": "exact", "candidates": 3}).get_json()
    assert r["results"][0]["sql"] == sqls[""]
    vote = next(s for s in r["steps"] if s["step"] == "candidate_vote")
    assert "3 条候选" in vote["info"] and "2 票" in vote["info"]


def test_variant_changes_planner_instruction(monkeypatch):
    seen = []

    def fake_once(prompt, steps):
        seen.append(prompt)
        return {"analyses": [{"sql": "SELECT 1"}]}
    monkeypatch.setattr(server, "_plan_once", fake_once)
    for v in ("", "probe", "decompose"):
        tok = server._PLAN_VARIANT.set(v)
        try:
            server.agent_sql_plan("q", "ctx", [], probe_runner=lambda s: "")
        finally:
            server._PLAN_VARIANT.reset(tok)
    assert len(set(seen)) == 3
