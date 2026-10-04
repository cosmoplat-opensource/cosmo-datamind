"""问数先探查、再作答、按结果核对(DR-080)。

BIRD 对比实验中,能先查看取值、试运行 SQL 的 ReAct 智能体明显优于单次生成;
DataMind 问数的主要失分是单次生成、召回的表不全时编造表名、结果多带列。"""
from __future__ import annotations

import json

import server


def _fake_llm(monkeypatch, replies):
    prompts = []

    def fake_turn(rt, sid, prompt, timeout, task=None):
        prompts.append(prompt)
        return True, replies[min(len(prompts), len(replies)) - 1]

    import agent_runtime
    monkeypatch.setattr(agent_runtime, "available", lambda: ["openai"])
    monkeypatch.setattr(agent_runtime, "get_runtime", lambda d: object())
    monkeypatch.setattr(server, "_drv_order", lambda: ["openai"])
    monkeypatch.setattr(server, "_llm_turn", fake_turn)
    return prompts


def test_planner_can_probe_values_before_answering(monkeypatch):
    prompts = _fake_llm(monkeypatch, [
        json.dumps({"probes": [{"why": "看状态取值", "sql": "SELECT DISTINCT status FROM fact_production_order"}]}),
        json.dumps({"analyses": [{"title": "答", "sql": "SELECT COUNT(*) FROM fact_production_order WHERE status='已完成'"}]}),
    ])
    ran = []
    plan = server.agent_sql_plan("已完成的工单有多少", "表 fact_production_order(...)", [],
                                 probe_runner=lambda sql: ran.append(sql) or "status: 已完成 / 生产中")
    assert ran == ["SELECT DISTINCT status FROM fact_production_order"]
    assert "status: 已完成 / 生产中" in prompts[1]                 # 探查结果进入下一轮
    assert plan["analyses"][0]["sql"].endswith("'已完成'")


def test_probe_rounds_are_bounded_and_final_round_demands_an_answer(monkeypatch):
    prompts = _fake_llm(monkeypatch, [json.dumps({"probes": [{"sql": "SELECT 1"}]})] * 5)
    server.agent_sql_plan("q", "ctx", [], probe_runner=lambda sql: "1")
    assert len(prompts) == server.MAX_PROBE_ROUNDS + 1
    assert "现在必须输出 analyses" in prompts[-1]


def test_probe_runner_is_read_only_and_reports_errors():
    out = server._probe_runner(None)("SELECT no_such_col FROM fact_sales_order")
    assert "错误" in out
    assert "拒绝" in server._probe_runner(None)("DELETE FROM fact_sales_order")
    rows = server._probe_runner(None)("SELECT DISTINCT status FROM fact_production_order")
    assert "已完成" in rows and rows.count("\n") <= 22                # 最多 20 行


def test_context_lists_every_ontology_table_name():
    ctx = server.build_context("各车间的产量")
    assert "库中其他表" in ctx
    for t in ("dim_downtime_reason", "fact_quality_inspection", "dim_supplier"):
        assert t in ctx


def test_exact_answer_is_checked_against_its_result_and_revised(monkeypatch):
    first = "SELECT status, COUNT(*) AS n FROM fact_production_order WHERE status='已完成' GROUP BY status"
    fixed = "SELECT COUNT(*) AS n FROM fact_production_order WHERE status='已完成'"
    monkeypatch.setattr(server, "agent_sql_plan", lambda *a, **k: {"analyses": [{"title": "答", "sql": first}]})
    seen = {}

    def fake_verify(question, ctx, sql, data):
        seen["cols"] = data["columns"]
        return fixed
    monkeypatch.setattr(server, "_verify_answer", fake_verify)
    r = server.app.test_client().post("/api/chat", json={"q": "已完成的工单有多少", "nocache": 1,
                                                         "answer_style": "exact"}).get_json()
    assert seen["cols"] == ["status", "n"]
    assert r["results"][0]["sql"] == fixed
    assert any(s["step"] == "self_check" and s["ok"] for s in r["steps"])


def test_bounded_worker_threads_see_request_context(make_sqlite):
    """问数在守护线程里调用规划与探查:线程必须带上请求的上下文变量(执行库、作答形式),
    否则探查查到主库、精确作答规则丢失(BIRD 对比实验中发现,DR-080)。"""
    path = make_sqlite({"t": ("a INTEGER", [(1,)])}, fname="ctx.db")
    t1, t2 = server._QA_DB.set(path), server._ANSWER_STYLE.set("exact")
    try:
        got = server._bounded(lambda: (server._qa_db(), server._ANSWER_STYLE.get()), 5)
        streamed = [e for e in server._bounded_stream(lambda log: (server._qa_db(), server._ANSWER_STYLE.get()), 5)
                    if e[0] == "done"][0][1]
    finally:
        server._QA_DB.reset(t1)
        server._ANSWER_STYLE.reset(t2)
    assert got == (path, "exact") and streamed == (path, "exact")


def test_words_inside_string_literals_are_not_parsed_as_tables():
    """WHERE Title = 'Eliciting priors from experts' 里的 from experts 不是表(DR-080)。"""
    import intent_check
    ir = server.load_ir_edited("demo")
    sql = "SELECT cust_name FROM dim_customer WHERE cust_name = 'Orders from experts' OR industry = 'join us'"
    ok, why = server._validate_sql_ontology(sql, ir, strict=True)
    assert ok, why
    assert intent_check.actual_intent(sql, ir)["tables"] == ["dim_customer"]


def test_date_columns_show_an_example_value():
    obj = {"table": "fact_sales_order", "attrs": [{"col": "order_date"}, {"col": "amount"}]}
    text = server._obj_cols_text(obj)
    assert "order_date(" in text and "取值示例:20" in text      # 让模型按实际格式写日期条件


def test_question_values_are_located_to_columns():
    """问句里的编号、引号内的名称、带小数的金额在库中查到出处后写进上下文。"""
    con = server.ro_connect(server.DB)
    code = con.execute("SELECT equipment_code FROM dim_equipment LIMIT 1").fetchone()[0]
    con.close()
    ctx = server.build_context(f"设备 {code} 去年停机多少小时?")
    assert "问句中的取值出处" in ctx and f"{code} → dim_equipment.equipment_code" in ctx


def test_exact_rules_exclude_nulls_when_ranking():
    assert "NULL" in server._EXACT_RULES
