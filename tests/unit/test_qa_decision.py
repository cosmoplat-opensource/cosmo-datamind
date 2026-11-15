# -*- coding: utf-8 -*-
"""问数使用决策层(DR-119):约束与规则按锚定对象带入、未接入属性拦截、业务词解析、问题分型、
判定 / 解释 / 追踪 / 异常汇总的确定性求解,以及依据卡。全部在合成库上运行,不调模型。"""
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
