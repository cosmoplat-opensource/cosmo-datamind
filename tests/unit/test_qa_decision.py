# -*- coding: utf-8 -*-
"""问数使用决策层(DR-119):约束与规则按锚定对象带入、未接入属性拦截、业务词解析、问题分型、
判定 / 解释 / 追踪 / 异常汇总的确定性求解,以及依据卡。全部在合成库上运行,不调模型。"""
import datetime as dt
import re
import sqlite3

import pytest

import qa_decision as qd
import server

RULES = [
    {"id": "r_late", "cn": "工序任务拖期", "on": "工序任务", "when": [{"field": "late_days", "op": ">", "value": 0}],
     "then": {"decision": "任务已拖期,需要重排", "action": "reschedule", "severity": "high"}},
    {"id": "r_urgent", "cn": "加急订单", "on": "订单", "when": [{"field": "priority", "op": "==", "value": "urgent"}],
     "then": {"decision": "优先排产", "severity": "medium"}},
]
ACTIONS = [{"id": "act_resched", "cn": "任务重排", "desc": "对拖期的工序任务重新排程", "risk": "low", "enabled": True}]


@pytest.fixture
def world(make_sqlite):
    db = make_sqlite({
        "demo_clock": ("today TEXT", [("2026-11-10",)]),
        "orders": ("order_no TEXT PRIMARY KEY, customer TEXT, due_date TEXT, priority TEXT",
                   [("P1001", "客户A", "2026-11-20", "urgent"), ("P1002", "客户B", "2026-12-05", "normal")]),
        "operations": ("op_code TEXT PRIMARY KEY, op_name TEXT", [("WIND", "绕线"), ("DRY", "干燥"), ("ASM", "总装")]),
        "tasks": ("task_id INTEGER PRIMARY KEY, order_no TEXT, op_code TEXT, plan_start TEXT, plan_end TEXT, late_days INTEGER, resource TEXT", [
            (1, "P1001", "WIND", "2026-11-01 08:00:00", "2026-11-05 17:00:00", 0, "绕线机1"),
            (2, "P1001", "DRY", "2026-11-08 07:00:00", "2026-11-12 07:00:00", 2, "干燥炉1"),
            (3, "P1001", "ASM", "2026-11-13 08:00:00", "2026-11-18 17:00:00", 0, "总装线"),
            (4, "P1002", "WIND", "2026-11-12 08:00:00", "2026-11-16 17:00:00", 0, "绕线机1"),
        ]),
    })
    ir = {"objects": [{"name": "订单", "table": "orders", "cn": "订单", "kind": "event"},
                      {"name": "工序", "table": "operations", "cn": "工序", "kind": "object"},
                      {"name": "工序任务", "table": "tasks", "cn": "工序任务", "kind": "event"}],
          "relations": [{"source_concept": "工序任务", "target_concept": "订单", "status": "verified", "overlap": 100.0,
                         "evidence": {"child_key": "order_no", "parent_key": "order_no"}},
                        {"source_concept": "工序任务", "target_concept": "工序", "status": "asserted",
                         "evidence": {"child_key": "op_code", "parent_key": "op_code"}}],
          "constraints": [
              {"id": "c_dry_wait", "cn": "干燥等待时限", "on": "工序任务", "kind": "batching", "hardness": "hard",
               "rule": "同批凑炉的等待不超过 72 小时", "params": {"max_wait_h": 72}, "status": "evidenced",
               "evidence": [{"type": "code", "source": "scheduler.py", "lines": "169-180"}]},
              {"id": "c_due", "cn": "交期约束", "on": "订单", "kind": "due_date", "hardness": "soft",
               "rule": "完工不晚于交期", "params": {}, "status": "candidate", "evidence": []}],
          "lighting": {"objects": {"订单": {"status": "lit"}, "工序任务": {"status": "partial", "source": "只有计划,没有报工"}},
                       "unlit": [{"attribute": "工序任务.实际开工 / 实际完工", "needs": "MES 报工", "why": "目前只有计划时间"}]},
          "clock": {"table": "demo_clock", "today": "today"},
          "main_chain": {"root": {"table": "orders", "key": "order_no", "label": "订单", "show": ["customer", "due_date"]},
                         "hops": [{"table": "tasks", "fk": "order_no", "label": "工序任务", "order_by": "plan_start",
                                   "start": "plan_start", "end": "plan_end", "step": "op_code", "resource": "resource",
                                   "show": ["op_code", "plan_start", "plan_end", "resource"],
                                   "lookups": [{"table": "operations", "local": "op_code", "key": "op_code", "show": {"op_name": "工序名"}}]}]},
          "concepts": [{"name": "加急单", "aliases": ["急单"], "table": "orders", "filter": "priority = 'urgent'"}]}
    connect = lambda: sqlite3.connect(f"file:{db}?mode=ro", uri=True)       # noqa: E731
    return {"db": db, "ir": ir, "connect": connect}


def _route(w, q):
    return qd.classify(q, w["ir"], RULES, ACTIONS, w["connect"])


def _answer(w, q, validate=None):
    route = _route(w, q)
    anchored = qd.anchored_objects(w["ir"], ["orders", "tasks"])
    return route, qd.answer(q, w["ir"], RULES, ACTIONS, route, w["connect"], anchored, validate=validate)


# ── 按锚定对象带入约束与规则 ────────────────────────────────────────

def test_only_constraints_and_rules_on_anchored_objects_are_carried(world):
    anchored = qd.anchored_objects(world["ir"], ["orders"])
    text, n, used = qd.decision_context("随便问点什么", world["ir"], RULES, anchored)
    assert used == {"constraints": ["c_due"], "rules": ["r_urgent"]} and n == 2
    assert "交期约束" in text and "干燥等待时限" not in text
    assert qd.decision_context("x", {"objects": []}, [], set()) == ("", 0, {"constraints": [], "rules": []})


# ── 未接入属性 ──────────────────────────────────────────────────────

def test_a_question_about_data_that_is_not_connected_is_not_answered_from_other_fields(world):
    route, ans = _answer(world, "P1001 各道工序的实际完工时间")
    assert route["type"] == "unlit"
    assert ans["results"] == [] and "MES 报工" in ans["narrative"] and "实际完工" in ans["narrative"]
    assert ans["evidence_card"]["type"] == "unlit"
    assert _route(world, "P1001 各道工序的计划完工时间")["type"] != "unlit"


# ── 业务词 ──────────────────────────────────────────────────────────

def test_time_words_use_the_graphs_clock_and_aliases_resolve_to_filters(world):
    text, terms = qd.resolve_terms("本周要开工的急单有哪些", world["ir"], world["connect"])
    assert [t["word"] for t in terms["time"]] == ["本周"] and "2026-11-09" in text and "2026-11-15" in text
    assert [a["word"] for a in terms["aliases"]] == ["急单"] and "priority = 'urgent'" in text
    plain = {"objects": world["ir"]["objects"]}
    assert qd.resolve_terms("本周要开工的任务", plain, world["connect"])[1]["time"] == []      # 没声明时钟的图谱不改写时间词


# ── 分型 ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("q,want", [
    ("哪些工序任务拖期了?", "judge"),
    ("订单 P1001 现在到哪道工序了?", "trace"),
    ("为什么干燥要等待 72 小时?", "explain"),
    ("现在有哪些异常?", "monitor"),
    ("各客户的订单数量是多少?", "lookup"),
    ("拖期最多的是哪些工序任务?", "lookup"),                       # 排名与汇总交给查数
    ("哪些工序任务拖期了,客户分别是谁?", "lookup"),                 # 一句问两件事交给查数
    ("订单 P9999 现在到哪道工序了?", "lookup"),                     # 编号不存在,不做追踪
])
def test_classification(world, q, want):
    assert _route(world, q)["type"] == want


def test_graphs_without_decision_content_always_fall_back_to_lookup(world):
    plain = {"objects": world["ir"]["objects"], "relations": world["ir"]["relations"]}
    for q in ("哪些工序任务拖期了?", "订单 P1001 现在到哪道工序了?", "现在有哪些异常?"):
        assert qd.classify(q, plain, [], [], world["connect"])["type"] == "lookup"


# ── 判定 ────────────────────────────────────────────────────────────

def test_judgement_matches_the_database_and_shows_its_sql(world):
    route, ans = _answer(world, "哪些工序任务拖期了?")
    truth = sqlite3.connect(world["db"]).execute("SELECT COUNT(*) FROM tasks WHERE late_days > 0").fetchone()[0]
    card = ans["evidence_card"]
    assert truth == 1 and dict(card["kv"])["命中"] == truth and dict(card["kv"])["总行数"] == 4
    assert [r["task_id"] for r in ans["results"][0]["data"]["rows"]] == [2]
    assert "late_days" in ans["results"][0]["sql"] and card["basis"][0]["id"] == "r_late"
    assert any(n.get("action_id") == "act_resched" for n in card["next"])        # 下一步:相关的已登记动作
    assert card["next"][0]["q"].startswith("订单 P1001")                          # 以及沿主线追踪第一条命中


def test_a_judgement_blocked_by_the_ontology_gate_goes_back_to_lookup(world):
    _route_, ans = _answer(world, "哪些工序任务拖期了?", validate=lambda sql: (False, "超出所选范围"))
    assert ans is None


def test_rule_sql_and_row_wise_evaluation_agree(world):
    rules = qd.rules_on_tables(RULES, world["ir"])
    ev = qd.evaluate_rule(rules[0], world["connect"])
    assert ev["method"] == "sql" and ev["count"] == 1 and ev["mismatch"] is False
    odd = dict(rules[0], when=[{"field": "resource", "op": "contains", "value": "干燥"}])
    assert qd.rule_sql(odd) is None and qd.evaluate_rule(odd, world["connect"])["count"] == 1     # 不能编译时逐行求值


# ── 解释 ────────────────────────────────────────────────────────────

def test_an_explanation_cites_the_constraint_and_its_evidence(world):
    route, ans = _answer(world, "为什么干燥要等待 72 小时?")
    assert route["type"] == "explain"
    basis = ans["evidence_card"]["basis"][0]
    assert basis["id"] == "c_dry_wait" and basis["evidence"] == "scheduler.py:169-180" and basis["params"] == {"max_wait_h": 72}
    assert "72 小时" in ans["narrative"]


def test_an_explanation_without_a_matching_constraint_or_rule_is_left_to_lookup(world):
    _route_, ans = _answer(world, "为什么客户A的销售额下降了?")
    assert ans is None


# ── 追踪 ────────────────────────────────────────────────────────────

def test_tracing_follows_verified_relations_and_reports_progress_against_the_clock(world):
    route, ans = _answer(world, "订单 P1001 现在到哪道工序了?")
    assert route == {"type": "trace", "root_id": "P1001"}
    kv = dict(ans["evidence_card"]["kv"])
    assert kv["订单"] == "P1001" and kv["已完成"] == 1 and kv["进行中"] == 1 and kv["未开工"] == 1
    assert "进行中 干燥(干燥炉1)" in ans["narrative"] and "下一道 总装" in ans["narrative"]
    assert ans["evidence_card"]["basis"][0]["status"] == "verified"
    rows = ans["results"][0]["data"]["rows"]
    assert [r["工序名"] for r in rows] == ["绕线", "干燥", "总装"] and [r["进度"] for r in rows] == ["已完成", "进行中", "未开工"]


def test_tracing_stops_at_a_hop_the_ontology_has_not_verified(world):
    ir = dict(world["ir"], relations=[dict(world["ir"]["relations"][0], status="candidate")])
    route = {"type": "trace", "root_id": "P1001"}
    ans = qd.answer("订单 P1001 现在到哪道工序了?", ir, RULES, ACTIONS, route, world["connect"], set())
    assert ans["results"] == [] and "未在本体中验证" in ans["narrative"]


# ── 异常汇总 ────────────────────────────────────────────────────────

def test_monitor_lists_every_rule_with_its_hit_count(world):
    _route_, ans = _answer(world, "现在有哪些异常?")
    rows = {r["规则"]: r["命中"] for r in ans["results"][0]["data"]["rows"]}
    assert rows == {"工序任务拖期": 1, "加急订单": 1} and ans["evidence_card"]["type"] == "monitor"


# ── 查数答案的依据卡 ────────────────────────────────────────────────

def test_lookup_card_names_constraints_used_and_flags_weak_spots(world):
    anchored = qd.anchored_objects(world["ir"], ["orders", "tasks"])
    _text, _n, used = qd.decision_context("q", world["ir"], RULES, anchored)
    results = [{"title": "各订单任务数", "sql": "SELECT order_no, COUNT(*) FROM tasks GROUP BY order_no", "data": {"rows": [{"n": 2}]}}]
    metrics = [{"name": "准时率", "status": "candidate", "caliber": "按计划完工"}]
    card = qd.lookup_card("各订单的任务数", results, world["ir"], used, metrics, anchored, [], RULES, ACTIONS, "共 2 张订单")
    kinds = [b["kind"] for b in card["basis"]]
    assert kinds[0] == "sql" and "constraint" in kinds and "metric" in kinds
    assert [b["id"] for b in card["basis"] if b["kind"] == "constraint"] == ["c_dry_wait"]       # 只列 SQL 实际用到的对象上的约束
    assert any("准时率" in f and "未核验" in f for f in card["flags"])
    assert any("工序任务" in f and "部分接入" in f for f in card["flags"])
    assert card["conclusion"] == "共 2 张订单"


# ── 服务端接入 ──────────────────────────────────────────────────────

@pytest.fixture
def wired(world, monkeypatch):
    monkeypatch.setattr(server, "_qa_db", lambda: world["db"])
    monkeypatch.setattr(server, "_load_rules", lambda key: RULES)
    import srv_actions
    monkeypatch.setattr(srv_actions, "load_action_types", lambda: ACTIONS)
    return world


def _prep(w, q):
    anchor = {"objects": [{"table": "orders"}, {"table": "tasks"}]}
    return server._qa_decision_prep(q, w["ir"], ["built_x"], anchor)


def test_server_prepares_context_route_and_steps(wired):
    prep = _prep(wired, "哪些工序任务拖期了?")
    assert prep["route"]["type"] == "judge" and prep["n"] == 4 and not prep["error"]
    steps = {s[0]: s for s in server._qa_decision_steps(prep)}
    assert "约束 2 条" in steps["decision_layer"][2] and "判定" in steps["route"][2]
    ans = server._qa_decision_answer("哪些工序任务拖期了?", wired["ir"], prep)
    assert ans["evidence_card"]["type"] == "judge"


def test_server_falls_back_to_lookup_when_the_solver_fails(wired, monkeypatch):
    prep = _prep(wired, "哪些工序任务拖期了?")
    monkeypatch.setattr(qd, "answer", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert server._qa_decision_answer("哪些工序任务拖期了?", wired["ir"], prep) is None
    assert "boom" in prep["error"] and any(s[0] == "decision_error" for s in server._qa_decision_steps(prep))


def test_plain_questions_on_plain_graphs_add_no_decision_steps(wired):
    plain = {"objects": wired["ir"]["objects"], "relations": wired["ir"]["relations"]}
    prep = server._qa_decision_prep("各客户的订单数量", plain, [], {"objects": [{"table": "orders"}]})
    assert prep["route"] == {"type": "lookup"} and server._qa_decision_steps(prep) == []


def test_lookup_answers_on_the_builtin_graph_carry_an_evidence_card(monkeypatch):
    monkeypatch.setattr(server, "agent_sql_plan", lambda *a, **k: None)
    monkeypatch.setattr(server, "_match_qa_skill", lambda _q: None)
    monkeypatch.setattr(server, "narrative_llm", lambda *a, **k: "ok")
    server._QA_CACHE.clear()
    d = server.app.test_client().post("/api/chat", json={"q": "各月收入趋势"}).get_json()
    assert d.get("results") and d["evidence_card"]["type"] == "lookup"
    assert d["evidence_card"]["basis"][0]["kind"] == "sql" and d["evidence_card"]["basis"][0]["text"] == d["results"][0]["sql"]


# ── 推演(DR-120) ───────────────────────────────────────────────────

TRIAL = {"id": "act_trial", "cn": "插单试算", "desc": "试算新增订单能否按期交付", "object": "订单", "risk": "low", "enabled": True,
         "params": [{"name": "customer", "cn": "客户", "type": "text", "required": True},
                    {"name": "due_date", "cn": "交期", "type": "text", "required": True}]}
RELEASE = {"id": "act_release", "cn": "插单发布", "desc": "把插单试算的结果发布到生产计划", "object": "订单", "risk": "high",
           "enabled": True, "params": [{"name": "customer", "cn": "客户", "type": "text", "required": True}]}
UNBOUND = {"id": "act_other", "cn": "插单试算(别的库)", "desc": "试算新增订单", "object": "别处的对象", "risk": "low", "enabled": True,
           "params": []}


@pytest.fixture
def trial_world(world):
    world["ir"]["param_grounding"] = {"customer": {"table": "orders", "key": "customer", "match": ["customer"]},
                                      "due_date": {"type": "date"}}
    return world


def _whatif(w, q, invoke, actions=(TRIAL, RELEASE, UNBOUND)):
    route = qd.classify(q, w["ir"], RULES, list(actions), w["connect"])
    ans = qd.answer(q, w["ir"], RULES, list(actions), route, w["connect"], set(), invoke=invoke, operator="计划员")
    return route, ans


def test_a_what_if_question_grounds_parameters_and_runs_the_low_risk_action_once(trial_world):
    calls = []

    def invoke(action_id, params, operator):
        calls.append((action_id, params, operator))
        return 200, {"id": "rec1", "status": "executed", "execution_mode": "executor", "real_writeback": False,
                     "result": {"ok": True, "summary": "可按期,最早 12 月 3 日完工",
                                "trial": {"promise": "可承诺", "earliest_finish": "2026-12-03", "late_days": -2,
                                          "plan": [{"工序": "绕线", "开工": "2026-11-16"}]}}}
    route, ans = _whatif(trial_world, "如果客户A新增一张订单,12月5日能否按期交付?", invoke)
    assert route["type"] == "whatif" and route["action"]["id"] == "act_trial"
    assert calls == [("act_trial", {"customer": "客户A", "due_date": "2026-12-05"}, "计划员")]
    card = ans["evidence_card"]
    assert card["type"] == "whatif" and dict(card["kv"])["承诺结论"] == "可承诺"
    assert card["basis"][0]["record_id"] == "rec1" and card["basis"][0]["real_writeback"] is False
    assert ans["results"][0]["data"]["rows"] == [{"工序": "绕线", "开工": "2026-11-16"}]
    assert "可按期" in ans["narrative"]


def test_missing_parameters_are_asked_for_instead_of_guessed(trial_world):
    calls = []
    route, ans = _whatif(trial_world, "如果新增一张订单能否按期交付?", lambda *a: calls.append(a) or (200, {}))
    assert route["type"] == "whatif" and calls == []
    assert ans["evidence_card"]["type"] == "clarify" and "客户" in ans["narrative"] and "交期" in ans["narrative"]


def test_a_high_risk_action_is_never_started_from_a_question(trial_world):
    calls = []
    _route_, ans = _whatif(trial_world, "如果客户A新增一张订单,能否插单发布?", lambda *a: calls.append(a) or (200, {}),
                           actions=(RELEASE,))
    assert calls == [] and "高风险" in ans["narrative"]
    assert ans["evidence_card"]["next"][0]["href"] == "#actioncenter"


def test_an_action_without_an_executor_says_it_was_only_recorded(trial_world):
    invoke = lambda *a: (200, {"id": "rec2", "status": "executed", "execution_mode": "decision_capture", "real_writeback": False})   # noqa: E731
    _route_, ans = _whatif(trial_world, "如果客户A新增一张订单,12月5日能否按期交付?", invoke)
    assert "没有配置执行器" in ans["narrative"] and ans["results"] == []


def test_an_executor_failure_is_reported_with_its_reason(trial_world):
    invoke = lambda *a: (200, {"id": "rec3", "status": "failed", "execution_mode": "executor",   # noqa: E731
                               "result": {"ok": False, "summary": "没有可用的干燥炉"}})
    _route_, ans = _whatif(trial_world, "如果客户A新增一张订单,12月5日能否按期交付?", invoke)
    assert "执行失败" in ans["narrative"] and "没有可用的干燥炉" in ans["narrative"]


@pytest.mark.parametrize("q", ["客户A上个月新增了几张订单?", "停机时长最长的设备是哪台?", "为什么新增订单不能按期交付?"])
def test_questions_about_what_already_happened_are_not_what_if(trial_world, q):
    assert qd.classify(q, trial_world["ir"], RULES, [TRIAL], trial_world["connect"])["type"] != "whatif"


def test_graphs_without_parameter_grounding_never_route_to_what_if(world):
    route = qd.classify("如果客户A新增一张订单,12月5日能否按期交付?", world["ir"], RULES, [TRIAL], world["connect"])
    assert route["type"] != "whatif"
    assert qd.bound_actions([TRIAL, UNBOUND], world["ir"]) == []


def test_server_passes_the_real_invoke_and_the_operator(wired, monkeypatch):
    wired["ir"]["param_grounding"] = {"customer": {"table": "orders", "key": "customer", "match": ["customer"]}, "due_date": {"type": "date"}}
    import bp_actions
    import srv_actions
    monkeypatch.setattr(srv_actions, "load_action_types", lambda: [TRIAL])
    seen = []
    monkeypatch.setattr(bp_actions, "invoke", lambda aid, params, op, simulate=False: seen.append((aid, op, simulate)) or (200, {
        "id": "r", "status": "executed", "execution_mode": "simulate", "result": {"ok": True, "summary": "可按期"}}))
    q = "如果客户A新增一张订单,12月5日能否按期交付?"
    with server.app.test_request_context("/api/chat", method="POST", json={"q": q, "operator": "张工"}):
        prep = _prep(wired, q)
        ans = server._qa_decision_answer(q, wired["ir"], prep, None, server._qa_operator({"operator": "张工"}))
    assert prep["route"]["type"] == "whatif" and ans["evidence_card"]["type"] == "whatif"
    assert seen == [("act_trial", "张工", True)]                                 # 问数只以只读试算的方式发起


def test_a_caller_that_came_through_the_agent_adapter_cannot_trigger_a_what_if(wired, monkeypatch):
    wired["ir"]["param_grounding"] = {"customer": {"table": "orders", "key": "customer", "match": ["customer"]}, "due_date": {"type": "date"}}
    import bp_actions
    import srv_actions
    monkeypatch.setattr(srv_actions, "load_action_types", lambda: [TRIAL])
    called = []
    monkeypatch.setattr(bp_actions, "invoke", lambda *a, **k: called.append(a) or (200, {}))
    q = "如果客户A新增一张订单,12月5日能否按期交付?"
    with server.app.test_request_context("/api/chat", method="POST", json={"q": q}, headers={"X-DataMind-Actor": "ath:agent-7:analyst"}):
        prep = _prep(wired, q)
        ans = server._qa_decision_answer(q, wired["ir"], prep)
    assert called == [] and "当前调用方不能触发" in ans["narrative"] and ans["results"] == []


def test_an_executor_not_marked_simulate_only_is_not_called_from_a_question(trial_world):
    refuse = lambda *a: (403, {"error": "x", "code": "simulate_not_allowed"})      # noqa: E731
    _route_, ans = _whatif(trial_world, "如果客户A新增一张订单,12月5日能否按期交付?", refuse)
    assert "没有标记为只做试算" in ans["narrative"] and ans["evidence_card"]["next"][0]["href"] == "#actioncenter"
    none = lambda *a: (409, {"error": "x", "code": "no_executor"})                 # noqa: E731
    assert "没有配置执行器" in _whatif(trial_world, "如果客户A新增一张订单,12月5日能否按期交付?", none)[1]["narrative"]


def test_an_unknown_outcome_is_reported_as_unknown_not_as_a_result(trial_world):
    lost = lambda *a: (200, {"id": "rec9", "status": "unknown", "execution_mode": "simulate", "result": {"error": "TimeoutError"}})   # noqa: E731
    _route_, ans = _whatif(trial_world, "如果客户A新增一张订单,12月5日能否按期交付?", lost)
    assert "结果未知" in ans["narrative"] and "rec9" in ans["narrative"] and ans["results"] == []


def test_the_model_is_never_offered_a_what_if_option(trial_world):
    opts = [k for k, _t, _r in qd.assist_options("展示客户清单", trial_world["ir"], RULES, [TRIAL], trial_world["connect"])]
    assert opts and not any(k.startswith("whatif") for k in opts)
    route = qd.classify_assisted("展示客户清单", trial_world["ir"], RULES, [TRIAL], trial_world["connect"],
                                 lambda p: '{"choice": "whatif:act_trial"}')
    assert route == {"type": "lookup"}


# ── 工艺路线即追踪主线(DR-121) ─────────────────────────────────────

import ontology_decision as od    # noqa: E402


def _route_only(world):
    ir = {k: v for k, v in world["ir"].items() if k != "main_chain"}
    ir["routes"] = [{"id": "main", "cn": "订单执行路线", "steps": ["订单", "工序任务", "工序"], "status": "candidate"}]
    return ir


def test_a_declared_route_is_enough_to_trace(world):
    ir = _route_only(world)
    chain = od.main_chain(ir)
    assert chain["root"] == {"table": "orders", "key": "order_no", "label": "订单"} and chain["derived_from_route"] == "main"
    assert [(h["table"], h["fk"], h["parent"], h["pk"]) for h in chain["hops"]] == [
        ("tasks", "order_no", "orders", "order_no"), ("operations", "op_code", "tasks", "op_code")]
    route = qd.classify("订单 P1001 现在到哪道工序了?", ir, RULES, ACTIONS, world["connect"])
    assert route == {"type": "trace", "root_id": "P1001"}
    ans = qd.answer("订单 P1001 现在到哪道工序了?", ir, RULES, ACTIONS, route, world["connect"], set())
    titles = [r["title"] for r in ans["results"]]
    assert titles == ["P1001 的工序任务", "P1001 的工序"]
    assert len(ans["results"][0]["data"]["rows"]) == 3 and len(ans["results"][1]["data"]["rows"]) == 3


def test_an_explicit_main_chain_wins_over_the_route(world):
    ir = dict(_route_only(world), main_chain=world["ir"]["main_chain"])
    assert "derived_from_route" not in od.main_chain(ir) and od.main_chain(ir)["hops"][0]["order_by"] == "plan_start"


def test_derivation_stops_where_the_relations_stop_and_ignores_retired_routes(world):
    ir = _route_only(world)
    ir["relations"] = [ir["relations"][0], dict(ir["relations"][1], status="candidate")]
    assert [h["table"] for h in od.main_chain(ir)["hops"]] == ["tasks"]              # 第二段只是候选,不沿它取数
    ir["routes"][0]["status"] = "deprecated"
    assert od.main_chain(ir) == {}
    assert od.main_chain({"objects": [], "routes": [{"id": "x", "steps": ["a", "b"]}]}) == {}


# ── 模型辅助分型(DR-123) ───────────────────────────────────────────

def _assist(w, q, reply, actions=ACTIONS):
    asked = []

    def ask(prompt):
        asked.append(prompt)
        return reply
    return qd.classify_assisted(q, w["ir"], RULES, list(actions), w["connect"], ask), asked


def test_a_rephrased_question_the_vocabulary_misses_can_still_be_judged(world):
    q = "拖期的工序任务都有谁"
    assert _route(world, q)["type"] == "lookup"                                  # 固定词表认不出
    route, asked = _assist(world, q, '{"choice": "judge:r_late"}')
    assert route["type"] == "judge" and route["rule"]["id"] == "r_late" and route["assisted"] is True
    assert "judge:r_late" in asked[0] and "explain:c_dry_wait" in asked[0] and "lookup" in asked[0]
    ans = qd.answer(q, world["ir"], RULES, ACTIONS, route, world["connect"], set())
    assert dict(ans["evidence_card"]["kv"])["命中"] == 1                         # 选中之后仍是确定性求解


def test_the_model_can_only_pick_what_the_graph_declares(world):
    for reply in ('{"choice": "judge:r_not_there"}', '{"choice": "delete_everything"}', "我觉得是判定", "", None,
                  '{"choice": "lookup"}', '{"choice": "trace"}'):                # 问句里没有编号,trace 不在选项里
        route, _ = _assist(world, "拖期的工序任务都有谁", reply)
        assert route == {"type": "lookup"}, reply


def test_an_explanation_picked_by_the_model_cites_that_constraint(world):
    q = "干燥凭啥要等那么久"
    route, _ = _assist(world, q, '{"choice": "explain:c_dry_wait"}')
    assert route["type"] == "explain" and route["pinned"] == ["c_dry_wait"]
    ans = qd.answer(q, world["ir"], RULES, ACTIONS, route, world["connect"], set())
    assert ans["evidence_card"]["basis"][0]["id"] == "c_dry_wait"


def test_tracing_is_offered_only_when_the_question_names_an_existing_record(world):
    route, asked = _assist(world, "P1001 做完没有", '{"choice": "trace"}')
    assert route == {"type": "trace", "root_id": "P1001", "assisted": True} and "追踪 P1001" in asked[0]


def test_plain_graphs_multi_part_questions_and_missing_data_never_ask_the_model(world):
    plain = {"objects": world["ir"]["objects"], "relations": world["ir"]["relations"]}
    calls = []
    ask = lambda p: calls.append(p) or '{"choice": "monitor"}'      # noqa: E731
    assert qd.classify_assisted("拖期的工序任务都有谁", plain, [], [], world["connect"], ask) == {"type": "lookup"}
    assert qd.classify_assisted("拖期的任务都有谁,客户分别是谁", world["ir"], RULES, ACTIONS, world["connect"], ask) == {"type": "lookup"}
    assert qd.classify_assisted("各工序的实际完工情况", world["ir"], RULES, ACTIONS, world["connect"], ask) == {"type": "lookup"}
    assert calls == []


def test_server_asks_the_model_only_after_the_vocabulary_gives_up(wired, monkeypatch):
    asked = []
    monkeypatch.setattr(server, "_qa_route_ask", lambda prompt, keys: asked.append(prompt) or '{"choice": "judge:r_late"}')
    assert _prep(wired, "哪些工序任务拖期了?")["route"].get("assisted") is None and asked == []   # 词表认得出的不问模型
    prep = _prep(wired, "拖期的工序任务都有谁")
    assert prep["route"]["type"] == "judge" and prep["route"]["assisted"] is True and len(asked) == 1
    assert any("模型辅助判断" in s[2] for s in server._qa_decision_steps(prep))
    monkeypatch.setenv("DATAMIND_QA_ROUTE_LLM", "0")
    assert _prep(wired, "拖期的工序任务都有谁")["route"] == {"type": "lookup"} and len(asked) == 1


def test_without_an_engine_the_assist_call_returns_nothing_quickly(monkeypatch):
    import agent_runtime
    monkeypatch.setattr(agent_runtime, "available", lambda: [])
    server._QA_ROUTE_CACHE.clear()
    assert server._qa_route_ask("任意提示", ["demo"]) is None and server._QA_ROUTE_CACHE == {}


# ── 追踪与工艺验收用同一条关系(DR-126) ─────────────────────────────

ROLE_IR = {"objects": [{"name": "工单", "table": "wo", "pk": "wo_id", "cn": "工单"}, {"name": "工序", "table": "op", "cn": "工序"}],
           "relations": [
               {"source_concept": "工单", "target_concept": "工序", "role": "首道工序", "status": "verified",
                "evidence": {"child_key": "first_op_id", "parent_key": "op_id"}},
               {"source_concept": "工单", "target_concept": "工序", "role": "末道工序", "status": "verified",
                "evidence": {"child_key": "last_op_id", "parent_key": "op_id"}}]}


def test_the_trace_follows_the_role_named_in_the_route():
    import cq_check
    last = {"id": "r", "steps": ["工单", "工序"], "roles": ["末道工序"]}
    hop = od.main_chain_from_route(ROLE_IR, last)["hops"][0]
    assert (hop["pk"], hop["role"], hop["direction"]) == ("last_op_id", "末道工序", "parent")
    first = od.main_chain_from_route(ROLE_IR, dict(last, roles=["首道工序"]))["hops"][0]
    assert first["pk"] == "first_op_id"
    assert cq_check.check_route(["工单", "工序"], ROLE_IR, ["末道工序"])["segments"][0]["role"] == hop["role"]   # 验收与追踪用的是同一个角色


def test_an_unnamed_role_between_two_candidate_relations_is_not_guessed():
    chain = od.main_chain_from_route(ROLE_IR, {"id": "r", "steps": ["工单", "工序"]})
    assert chain == {}                                                    # 第一段就有歧义:不生成主线
    ir = {"objects": ROLE_IR["objects"] + [{"name": "设备", "table": "eq", "cn": "设备"}],
          "relations": [ROLE_IR["relations"][0],
                        {"source_concept": "工序", "target_concept": "设备", "role": "主设备", "status": "verified", "evidence": {"child_key": "eq_id", "parent_key": "eq_id"}},
                        {"source_concept": "工序", "target_concept": "设备", "role": "备用设备", "status": "verified", "evidence": {"child_key": "spare_eq_id", "parent_key": "eq_id"}}]}
    chain = od.main_chain_from_route(ir, {"id": "r", "steps": ["工单", "工序", "设备"], "roles": ["首道工序"]})
    assert [h["table"] for h in chain["hops"]] == ["op"]
    assert chain["stopped"] == {"after": "工序", "before": "设备", "reason": "这两步之间有不止一条可用的关系,请在路线里指明角色"}


def test_a_trace_reports_the_role_it_used_and_where_the_route_stopped(make_sqlite):
    db = make_sqlite({"wo": ("wo_id TEXT PRIMARY KEY, first_op_id TEXT, last_op_id TEXT", [("W1", "OP1", "OP9")]),
                      "op": ("op_id TEXT PRIMARY KEY, op_name TEXT", [("OP1", "绕线"), ("OP9", "试验")])})
    connect = lambda: sqlite3.connect(f"file:{db}?mode=ro", uri=True)      # noqa: E731
    ir = dict(ROLE_IR, routes=[{"id": "r", "cn": "到末道工序", "steps": ["工单", "工序"], "roles": ["末道工序"], "status": "candidate"}])
    route = qd.classify("工单 W1 现在到哪了?", ir, [], [], connect)
    assert route == {"type": "trace", "root_id": "W1"}
    ans = qd.answer("工单 W1 现在到哪了?", ir, [], [], route, connect, set())
    assert [r["op_name"] for r in ans["results"][0]["data"]["rows"]] == ["试验"]          # 取的是末道工序,不是首道
    basis = ans["evidence_card"]["basis"][0]
    assert basis["role"] == "末道工序" and "末道工序" in basis["text"] and "last_op_id" in basis["text"]


# ── 推演的参数必须唯一确定(DR-127) ───────────────────────────────────

@pytest.fixture
def twin_world(world):
    """两个同名客户;动作用客户编号。"""
    con = sqlite3.connect(world["db"])
    con.execute("CREATE TABLE customers (cust_id TEXT PRIMARY KEY, name TEXT, city TEXT)")
    con.executemany("INSERT INTO customers VALUES (?,?,?)", [("C001", "华东电力", "南京"), ("C002", "华东电力", "上海"), ("C003", "北方重工", "沈阳")])
    con.commit()
    con.close()
    world["ir"]["param_grounding"] = {"customer": {"table": "customers", "key": "cust_id", "match": ["name"], "show": ["city"]},
                                      "due_date": {"type": "date"}}
    return world


def _never(*a):
    raise AssertionError("参数没有确定之前不应调用动作")


def test_two_customers_with_the_same_name_are_offered_as_a_choice(twin_world):
    q = "如果华东电力新增一张订单,12月5日能否按期交付?"
    _route_, ans = _whatif(twin_world, q, _never)
    card = ans["evidence_card"]
    assert card["type"] == "clarify" and "对应 2 个客户" in ans["narrative"] and "C001(华东电力·南京)" in ans["narrative"]
    assert [n["q"] for n in card["next"]] == [f"{q}(客户取 C001)", f"{q}(客户取 C002)"]
    assert dict(card["kv"])["交期"] == "2026-12-05"                       # 已经确定的参数照常列出
    calls = []
    _route_, ans = _whatif(twin_world, card["next"][1]["q"], lambda *a: calls.append(a) or (200, {"id": "r", "status": "executed", "execution_mode": "simulate", "result": {"ok": True, "summary": "可按期"}}))
    assert calls == [("act_trial", {"customer": "C002", "due_date": "2026-12-05"}, "计划员")]
    kv = dict(ans["evidence_card"]["kv"])
    assert kv["客户"] == "C002" and kv["交期"] == "2026-12-05" and kv["数据日期"].startswith("2026-11-10")


def test_a_customer_named_by_its_number_is_unique(twin_world):
    calls = []
    _whatif(twin_world, "如果客户 C001 新增一张订单,12月5日能否按期交付?", lambda *a: calls.append(a) or (200, {}))
    assert calls[0][1]["customer"] == "C001"


def test_two_dates_in_one_question_are_offered_as_a_choice(twin_world):
    q = "如果北方重工新增一张订单,原定12月5日,现在想提前到11月28日,能否按期交付?"
    _route_, ans = _whatif(twin_world, q, _never)
    card = ans["evidence_card"]
    assert card["type"] == "clarify" and "问句里有 2 个日期" in ans["narrative"]
    assert [n["q"] for n in card["next"]] == [f"{q}(交期取 2026-12-05)", f"{q}(交期取 2026-11-28)"]
    calls = []
    _whatif(twin_world, card["next"][1]["q"], lambda *a: calls.append(a) or (200, {}))
    assert calls[0][1] == {"customer": "C003", "due_date": "2026-11-28"}


@pytest.mark.parametrize("text,expect,note", [
    ("1月5日", "2027-01-05", "按 2027 年理解"),             # 跨年:今年的 1 月 5 日已经过去
    ("11月10日", "2026-11-10", "按 2026 年理解"),            # 当天不算过去
    ("2027年2月28日", "2027-02-28", ""),
    ("本月底", "2026-11-30", "推算"),
    ("下个月底", "2026-12-31", "推算"),
    ("年底", "2026-12-31", "推算"),
    ("两周后", "2026-11-24", "起算"),
])
def test_dates_are_resolved_against_the_data_clock_and_say_what_was_assumed(text, expect, note):
    v, why, cands = qd.ground_param("due_date", {"type": "date"}, f"如果新增一张订单,{text}能否交付?", None, dt.date(2026, 11, 10), "交期")
    assert (v, cands) == (expect, []) and note in why


@pytest.mark.parametrize("text", ["11月31日", "2027年2月30日", "13月1日"])
def test_a_date_that_does_not_exist_is_reported_not_moved(text):
    v, why, _c = qd.ground_param("due_date", {"type": "date"}, f"如果新增一张订单,{text}能否交付?", None, dt.date(2026, 11, 10), "交期")
    assert v is None and "不是有效日期" in why


def test_a_specification_fragment_matching_several_rows_is_not_resolved_to_the_smallest_id(make_sqlite):
    db = make_sqlite({"products": ("pid TEXT PRIMARY KEY, spec TEXT", [("P2", "SZ11-63MVA/110kV"), ("P1", "SFZ-63MVA/110kV"), ("P3", "S13-20MVA/35kV")])})
    connect = lambda: sqlite3.connect(db)                                  # noqa: E731
    spec = {"table": "products", "key": "pid", "match": ["spec"]}
    v, why, cands = qd.ground_param("product", spec, "如果新增一台 63MVA/110kV 的订单能否交付?", connect, dt.date(2026, 11, 10), "产品")
    assert v is None and "对应 2 个产品" in why and [c["value"] for c in cands] == ["P1", "P2"]
    assert qd.ground_param("product", spec, "如果新增一台 20MVA/35kV 的订单能否交付?", connect, dt.date(2026, 11, 10), "产品")[0] == "P3"


def test_a_root_without_a_declared_key_takes_the_table_primary_key_not_the_foreign_key(make_sqlite):
    """构建出来的对象通常不带主键声明;根对象在第一段关系里是子表时,不能把它的外键列当成标识列。"""
    db = make_sqlite({"wo": ("wo_id TEXT PRIMARY KEY, first_op_id TEXT, last_op_id TEXT", [("W1", "OP1", "OP9")]),
                      "op": ("op_id TEXT PRIMARY KEY, op_name TEXT", [("OP1", "绕线"), ("OP9", "试验")]),
                      "nokey": ("a TEXT, b TEXT", [("x", "OP1")])})
    connect = lambda: sqlite3.connect(f"file:{db}?mode=ro", uri=True)      # noqa: E731
    objs = [{"name": "工单", "table": "wo", "cn": "工单"}, {"name": "工序", "table": "op", "cn": "工序"}]
    ir = {"objects": objs, "relations": ROLE_IR["relations"],
          "routes": [{"id": "r", "steps": ["工单", "工序"], "roles": ["末道工序"], "status": "candidate"}]}
    assert od.main_chain(ir)["root"]["key"] == ""                         # 图谱里没有可用的标识列:留空,不取 last_op_id
    route = qd.classify("工单 W1 现在到哪了?", ir, [], [], connect)
    assert route == {"type": "trace", "root_id": "W1"}
    ans = qd.answer("工单 W1 现在到哪了?", ir, [], [], route, connect, set())
    assert [r["op_name"] for r in ans["results"][0]["data"]["rows"]] == ["试验"]
    bare = {"objects": [{"name": "工单", "table": "nokey", "cn": "工单"}, objs[1]],
            "relations": [{"source_concept": "工单", "target_concept": "工序", "status": "verified", "evidence": {"child_key": "b", "parent_key": "op_id"}}],
            "routes": [{"id": "r", "steps": ["工单", "工序"]}]}
    assert qd.classify("工单 x 现在到哪了?", bare, [], [], connect)["type"] != "trace"      # 表也没有主键:不追踪


# ── 读数之前先校验范围(DR-129) ─────────────────────────────────────

def _spy(world):
    """记录实际发给数据库的语句。"""
    seen = []

    def connect():
        con = world["connect"]()
        con.set_trace_callback(seen.append)
        return con
    return connect, seen


def _reads(seen, table):
    return [s for s in seen if re.search(r'FROM\s+"?' + table + r'\b', s, re.I)]


def _scoped(world, q, allowed, validate=None):
    connect, seen = _spy(world)
    route = _route(world, q)
    seen.clear()
    scope = lambda t: (t in allowed, f"表 {t} 不在显式选择的数据表范围内")      # noqa: E731
    ans = qd.answer(q, world["ir"], RULES, ACTIONS, route, connect, set(), validate=validate, scope=scope)
    return ans, seen


def test_a_judgement_on_a_table_outside_the_selection_reads_nothing(world):
    ans, seen = _scoped(world, "哪些工序任务拖期了?", {"orders"})
    assert ans["results"] == [] and "tasks" in ans["narrative"] and "不在" in ans["narrative"]
    assert ans["evidence_card"]["type"] == "judge" and _reads(seen, "tasks") == []


def test_a_judgement_is_validated_before_it_is_evaluated(world):
    ans, seen = _scoped(world, "哪些工序任务拖期了?", {"orders", "tasks"}, validate=lambda sql: (False, "缺少有效行条件"))
    assert ans is None and _reads(seen, "tasks") == []


def test_monitor_only_evaluates_rules_inside_the_selection(world):
    ans, seen = _scoped(world, "现在有哪些异常?", {"orders"})
    rows = {r["规则"]: r["命中"] for r in ans["results"][0]["data"]["rows"]}
    assert rows == {"加急订单": 1} and _reads(seen, "tasks") == []
    assert "工序任务拖期" in ans["narrative"] and "不在" in ans["narrative"]             # 没判定的规则要说出来
    assert any("tasks" in f for f in ans["evidence_card"]["flags"])


def test_a_trace_that_leaves_the_selection_reads_nothing(world):
    ans, seen = _scoped(world, "订单 P1001 现在到哪道工序了?", {"orders"})
    assert ans["results"] == [] and "tasks" in ans["narrative"] and "不在" in ans["narrative"]
    assert _reads(seen, "tasks") == [] and _reads(seen, "orders") == [] and _reads(seen, "operations") == []


def test_without_a_selection_everything_is_evaluated_as_before(world):
    ans, _seen = _scoped(world, "现在有哪些异常?", {"orders", "tasks", "operations", "demo_clock"})
    assert {r["规则"] for r in ans["results"][0]["data"]["rows"]} == {"工序任务拖期", "加急订单"}
