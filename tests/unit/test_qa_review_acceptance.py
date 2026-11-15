# -*- coding: utf-8 -*-
"""贯通验收(DR-124 ~ DR-128):一张图谱里同时有首道 / 末道工序两条关系、两个同名客户,问句里有两个交期。
从问数入口进去,经只读试算,回到表格与依据卡,核对权限、角色、参数和结果前后一致。全部在合成库上运行,不调模型。"""
import json
import sqlite3

import pytest

import bp_actions
import server

TRIAL = {"id": "act_trial", "cn": "插单试算", "desc": "试算新增订单能否按期交付", "object": "订单", "risk": "low", "enabled": True,
         "params": [{"name": "customer", "cn": "客户", "type": "text", "required": True},
                    {"name": "due_date", "cn": "交期", "type": "text", "required": True}], "effects": []}
IR = {"objects": [{"name": "订单", "table": "orders", "pk": "order_no", "cn": "订单", "kind": "event"},
                  {"name": "工序", "table": "ops", "cn": "工序", "kind": "object"},
                  {"name": "客户", "table": "customers", "cn": "客户", "kind": "object"}],
      "relations": [
          {"source_concept": "订单", "target_concept": "工序", "role": "首道工序", "status": "verified",
           "evidence": {"child_key": "first_op_id", "parent_key": "op_id"}},
          {"source_concept": "订单", "target_concept": "工序", "role": "末道工序", "status": "verified",
           "evidence": {"child_key": "last_op_id", "parent_key": "op_id"}}],
      "routes": [{"id": "r_last", "cn": "订单到末道工序", "steps": ["订单", "工序"], "roles": ["末道工序"], "status": "candidate"}],
      "clock": {"table": "demo_clock", "today": "today"},
      "param_grounding": {"customer": {"table": "customers", "key": "cust_id", "match": ["name"], "show": ["city"]},
                          "due_date": {"type": "date"}}}
ASK = "如果华东电力新增一张订单,原定12月5日,现在想提前到11月28日,能否按期交付?"


class _Resp:
    def __init__(self, body):
        self._b = json.dumps(body).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def plant(make_sqlite, tmp_path, monkeypatch):
    db = make_sqlite({
        "demo_clock": ("today TEXT", [("2026-11-10",)]),
        "orders": ("order_no TEXT PRIMARY KEY, cust_id TEXT, first_op_id TEXT, last_op_id TEXT", [("P1001", "C001", "OP1", "OP9")]),
        "ops": ("op_id TEXT PRIMARY KEY, op_name TEXT", [("OP1", "绕线"), ("OP9", "出厂试验")]),
        "customers": ("cust_id TEXT PRIMARY KEY, name TEXT, city TEXT",
                      [("C001", "华东电力", "南京"), ("C002", "华东电力", "上海"), ("C003", "北方重工", "沈阳")]),
    })
    import srv_actions
    log = tmp_path / "action_log.json"
    log.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(bp_actions, "ACTION_LOG_F", str(log))
    monkeypatch.setattr(bp_actions, "load_action_log", lambda: json.loads(log.read_text(encoding="utf-8")))
    for mod in (bp_actions, srv_actions):
        monkeypatch.setattr(mod, "load_action_types", lambda: [TRIAL])
    monkeypatch.setattr(server, "_qa_db", lambda: db)
    monkeypatch.setattr(server, "_load_rules", lambda key: [])
    monkeypatch.setattr(server, "_qa_anchor_ir", lambda keys=None: (IR, ["built_e2e"], "", {}))
    monkeypatch.setattr(server, "_qa_scope_error", lambda *a, **k: None)
    monkeypatch.setattr(server, "build_context", lambda *a, **k: "")
    monkeypatch.setattr(server, "_qa_route_assist_enabled", lambda: False)
    monkeypatch.setattr(server, "_metric_cards", lambda *a, **k: [])
    server._QA_CACHE.clear()
    calls = []

    def executor(simulate_only=True):
        monkeypatch.setenv("DATAMIND_ACTION_EXECUTORS", json.dumps({"act_trial": {"url": "http://127.0.0.1:8096/trial", "simulate_only": simulate_only}}))

        def fake(req, timeout=None):
            body = json.loads(req.data)
            calls.append(body)
            return _Resp({"ok": True, "real_writeback": False, "summary": "可按期,比交期早 2.5 天",
                          "trial": {"promise": "可承诺", "earliest_finish": "2026-11-25", "late_days": -2.5,
                                    "plan": [{"工序": "绕线", "开工": "2026-11-12"}, {"工序": "出厂试验", "开工": "2026-11-24"}]}})
        monkeypatch.setattr(bp_actions, "_OPENER", type("Opener", (), {"open": staticmethod(fake)})())
    executor()
    client = server.app.test_client()

    def ask(q, headers=None):
        return client.post("/api/chat", json={"q": q, "nocache": True, "operator": "计划员"}, headers=headers or {}).get_json()
    return {"ask": ask, "calls": calls, "log": lambda: json.loads(log.read_text(encoding="utf-8")), "executor": executor,
            "db": db}


def _option(card, prefix):
    return next(n["q"] for n in card["next"] if n["label"].startswith(prefix))


def test_trace_uses_the_role_named_in_the_route_end_to_end(plant):
    d = plant["ask"]("订单 P1001 现在到哪了?")
    assert d["evidence_card"]["type"] == "trace"
    assert [r["op_name"] for r in d["results"][0]["data"]["rows"]] == ["出厂试验"]            # 末道工序,不是首道
    basis = d["evidence_card"]["basis"][0]
    assert basis["role"] == "末道工序" and "last_op_id" in basis["text"]
    assert d["answer_record"]["results"][0]["result_id"] == d["results"][0]["result_id"]


def test_what_if_asks_until_customer_and_date_are_unique_then_simulates_once(plant):
    d = plant["ask"](ASK)
    card = d["evidence_card"]
    assert card["type"] == "clarify" and "对应 2 个客户" in d["narrative"] and "问句里有 2 个日期" in d["narrative"]
    assert plant["calls"] == [] and plant["log"]() == []                                 # 没问清楚之前什么都不调用、不留记录

    d = plant["ask"](_option(card, "客户:C002"))
    card = d["evidence_card"]
    assert card["type"] == "clarify" and dict(card["kv"])["客户"] == "C002" and "对应 2 个客户" not in d["narrative"]
    assert plant["calls"] == []

    d = plant["ask"](_option(card, "交期:2026-11-28"))
    card = d["evidence_card"]
    assert card["type"] == "whatif" and len(plant["calls"]) == 1
    sent = plant["calls"][0]
    assert sent["mode"] == "simulate" and sent["params"] == {"customer": "C002", "due_date": "2026-11-28"}
    record = plant["log"]()
    assert len(record) == 1 and record[0]["status"] == "executed" and record[0]["execution_mode"] == "simulate"
    assert record[0]["operator"] == "计划员" and record[0]["params"] == sent["params"]
    kv = dict(card["kv"])                                                                 # 依据卡上的参数、数据日期与实际发出的一致
    assert (kv["客户"], kv["交期"], kv["承诺结论"]) == ("C002", "2026-11-28", "可承诺") and kv["数据日期"].startswith("2026-11-10")
    assert card["basis"][0]["record_id"] == record[0]["id"] and card["basis"][0]["real_writeback"] is False
    assert [r["工序"] for r in d["results"][0]["data"]["rows"]] == ["绕线", "出厂试验"]      # 表格就是执行器回执里的计划
    check = d["answer_record"]["narrative_check"]
    assert check["unverified"] == [] and {"token": "2.5", "source": "依据卡中的值"} in check["matched"]


def test_a_request_that_came_through_the_agent_adapter_cannot_simulate(plant):
    q = "如果北方重工新增一张订单,12月5日能否按期交付?"
    d = plant["ask"](q, headers={"X-DataMind-Actor": "ath:agent-7:analyst"})
    assert "当前调用方不能触发" in d["narrative"] and plant["calls"] == [] and plant["log"]() == []
    assert plant["ask"](q)["evidence_card"]["type"] == "whatif" and len(plant["calls"]) == 1   # 同一句话,界面上的使用者可以试算


def test_an_executor_that_is_not_simulate_only_is_never_called_from_a_question(plant):
    plant["executor"](simulate_only=False)
    d = plant["ask"]("如果北方重工新增一张订单,12月5日能否按期交付?")
    assert "没有标记为只做试算" in d["narrative"] and plant["calls"] == [] and plant["log"]() == []
    assert d["evidence_card"]["next"][0]["href"] == "#actioncenter"


def test_a_wrong_number_in_the_summary_is_reported_as_a_value_mismatch(plant, monkeypatch):
    con = sqlite3.connect(plant["db"])
    con.execute("CREATE TABLE sales (customer TEXT, profit REAL)")
    con.executemany("INSERT INTO sales VALUES (?,?)", [("华东电力", 3521.8), ("北方重工", -350.5)])
    con.commit()
    con.close()
    rows = [{"customer": "华东电力", "profit": 3521.8}, {"customer": "北方重工", "profit": -350.5}]
    resp = {"results": [{"title": "客户利润", "sql": "SELECT customer, profit FROM sales", "data": {"columns": ["customer", "profit"], "rows": rows}}],
            "narrative": "华东电力利润 3521.8,北方重工利润 350.5。", "evidence_card": {"basis": [], "kv": [], "flags": []}}
    with server.app.test_request_context("/api/chat", method="POST", json={"q": "各客户利润"}):
        out = server._qa_attach_record(resp, "各客户利润", ["built_e2e"])
    issues = out["answer_record"]["narrative_check"]["issues"]
    assert [(i["token"], i["kind"]) for i in issues] == [("350.5", "sign")]
    assert out["evidence_card"]["flags"][0].startswith("数值匹配")
