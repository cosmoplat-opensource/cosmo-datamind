# -*- coding: utf-8 -*-
"""可执行动作(DR-120):动作类型在部署配置里指定了本机执行器时,低风险在发起时调用,高风险在批准后调用;
没有配置时与此前一样只记录。执行器地址不能由请求方指定,也不能指向本机以外。"""
import io
import json
import urllib.error
from pathlib import Path

import pytest

import bp_actions
import server

LOW = {"id": "act_trial", "cn": "插单试算", "desc": "试算新增订单能否按期", "object": "订单", "risk": "low", "enabled": True,
       "params": [{"name": "product", "cn": "产品", "type": "text", "required": True}], "effects": []}
HIGH = {"id": "act_release", "cn": "发布计划", "desc": "把试算结果发布到生产计划", "object": "订单", "risk": "high", "enabled": True,
        "params": [], "effects": []}


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
def actions(tmp_path, monkeypatch):
    log = tmp_path / "action_log.json"
    log.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(bp_actions, "ACTION_LOG_F", str(log))
    monkeypatch.setattr(bp_actions, "load_action_types", lambda: [LOW, HIGH])
    monkeypatch.setattr(bp_actions, "load_action_log", lambda: json.loads(log.read_text(encoding="utf-8")))
    calls = []

    def configure(reply=None, error=None, conf=None, during=None):
        monkeypatch.setenv("DATAMIND_ACTION_EXECUTORS", json.dumps(conf if conf is not None else {
            "act_trial": "http://127.0.0.1:8096/trial", "act_release": "http://localhost:8096/release"}))

        def fake(req, timeout=None):
            calls.append({"url": req.full_url, "body": json.loads(req.data), "timeout": timeout})
            if during:
                during()
            if error:
                raise error
            return _Resp(reply if reply is not None else {"ok": True, "summary": "可按期", "real_writeback": False})
        monkeypatch.setattr(bp_actions, "_OPENER", type("Opener", (), {"open": staticmethod(fake)})())
    return {"configure": configure, "calls": calls, "client": server.app.test_client(),
            "log": lambda: json.loads(log.read_text(encoding="utf-8"))}


def _invoke(a, action_id="act_trial", params=None):
    return a["client"].post("/api/action/invoke", json={"action_id": action_id, "params": params or {"product": "T-100"}, "operator": "计划员"})


def test_without_an_executor_an_action_is_only_recorded(actions, monkeypatch):
    monkeypatch.delenv("DATAMIND_ACTION_EXECUTORS", raising=False)
    d = _invoke(actions).get_json()
    assert d["ok"] and d["execution_mode"] == "decision_capture" and d["real_writeback"] is False and "result" not in d
    assert actions["calls"] == []


def test_a_low_risk_action_calls_its_executor_and_keeps_the_result(actions):
    actions["configure"](reply={"ok": True, "summary": "最早 12 月 3 日完工", "real_writeback": False, "effects": ["试算 1 次"],
                               "trial": {"promise": "可承诺", "late_days": -2}})
    d = _invoke(actions).get_json()
    assert d["ok"] and d["execution_mode"] == "executor" and d["result"]["trial"]["promise"] == "可承诺"
    call = actions["calls"][0]
    assert call["url"] == "http://127.0.0.1:8096/trial" and call["body"]["params"] == {"product": "T-100"}
    assert call["body"]["operator"] == "计划员" and call["body"]["record_id"] == d["id"]
    assert call["body"]["idempotency_key"] == d["id"] and call["body"]["mode"] == "execute"
    rec = actions["log"]()[0]
    assert rec["status"] == "executed" and rec["effects"] == ["试算 1 次"] and "executor_seconds" in rec


def test_an_executor_failure_is_recorded_not_raised(actions):
    actions["configure"](reply={"ok": False, "summary": "没有可用的干燥炉"})
    d = _invoke(actions).get_json()
    assert d["ok"] is False and d["status"] == "failed" and d["result"]["summary"] == "没有可用的干燥炉"
    actions["configure"](error=urllib.error.HTTPError("u", 422, "bad", {}, io.BytesIO(json.dumps({"summary": "参数不合法"}).encode())))
    d = _invoke(actions).get_json()
    assert d["status"] == "failed" and d["result"] == {"error": "HTTP 422", "summary": "参数不合法"}
    actions["configure"](error=ConnectionRefusedError("refused"))                # 没有送达:可以确定没执行
    d = _invoke(actions).get_json()
    assert d["status"] == "failed" and "ConnectionRefusedError" in d["result"]["error"]
    assert [r["status"] for r in actions["log"]()] == ["failed", "failed", "failed"]


def test_a_high_risk_action_runs_only_after_approval(actions):
    actions["configure"](reply={"ok": True, "summary": "已发布", "real_writeback": True})
    d = _invoke(actions, "act_release", {}).get_json()
    assert d["status"] == "pending" and actions["calls"] == []
    denied = _invoke(actions, "act_release", {}).get_json()
    actions["client"].post("/api/action/approve", json={"id": denied["id"], "decision": "deny", "approver": "主管", "comment": "本周不发"})
    assert actions["calls"] == []
    r = actions["client"].post("/api/action/approve", json={"id": d["id"], "decision": "approve", "approver": "主管"}).get_json()
    assert r["ok"] and r["status"] == "executed" and r["result"]["summary"] == "已发布"
    assert len(actions["calls"]) == 1 and actions["calls"][0]["url"] == "http://localhost:8096/release"
    rec = next(x for x in actions["log"]() if x["id"] == d["id"])
    assert rec["real_writeback"] is True and rec["execution_mode"] == "executor" and rec["approver"] == "主管"


@pytest.mark.parametrize("url", ["http://10.0.0.5:8096/x", "http://example.com/x", "file:///etc/passwd", "ftp://127.0.0.1/x", "", 7])
def test_only_loopback_executors_from_deployment_config_are_used(actions, url):
    actions["configure"](conf={"act_trial": url})
    d = _invoke(actions).get_json()
    assert d["execution_mode"] == "decision_capture" and actions["calls"] == []


def test_a_caller_cannot_name_an_executor(actions, monkeypatch):
    monkeypatch.delenv("DATAMIND_ACTION_EXECUTORS", raising=False)
    r = actions["client"].post("/api/action/invoke", json={"action_id": "act_trial", "params": {"product": "T-100"}, "operator": "x",
                                                           "executor": "http://127.0.0.1:8096/evil", "executor_url": "http://127.0.0.1:1/"})
    assert r.get_json()["execution_mode"] == "decision_capture" and actions["calls"] == []


def test_malformed_config_means_no_executors(actions, monkeypatch):
    monkeypatch.setenv("DATAMIND_ACTION_EXECUTORS", "{not json")
    assert bp_actions._executor_url("act_trial") is None
    monkeypatch.setenv("DATAMIND_ACTION_EXECUTORS", '["act_trial"]')
    assert bp_actions._executor_url("act_trial") is None


def test_the_action_list_says_which_types_have_executors_and_counts_failures(actions):
    actions["configure"](reply={"ok": False, "summary": "x"})
    _invoke(actions)
    d = actions["client"].get("/api/actions").get_json()
    assert d["failed"] == 1 and d["execution"]["executors"] == ["act_release", "act_trial"]


def test_the_action_center_shows_every_status():
    from pathlib import Path
    ui = (Path(__file__).resolve().parents[2] / "ui/index.html").read_text(encoding="utf-8")
    start = ui.index("const SB={pending:")                      # 动作中心的状态表(页面里另有同名的关系状态表)
    table = ui[start:ui.index("};", start)]
    for status in bp_actions.ACTION_STATUSES:
        assert status + ":" in table, status
    assert "function acToast(" in ui and "执行失败" in ui


# ── 状态先落盘、按回执定结果(DR-125) ───────────────────────────────

def test_the_record_is_on_disk_as_executing_before_the_executor_is_called(actions):
    seen = []
    actions["configure"](during=lambda: seen.append([(r["status"], bool(r.get("exec_started"))) for r in actions["log"]()]))
    d = _invoke(actions).get_json()
    assert seen == [[("executing", True)]]                                       # 调用期间日志里已有这条记录
    assert d["status"] == "executed" and [r["status"] for r in actions["log"]()] == ["executed"]


@pytest.mark.parametrize("reply", [{"summary": "好像成功了"}, {"ok": "yes"}, {"ok": None, "summary": "x"}])
def test_a_receipt_without_a_clear_ok_is_not_treated_as_executed(actions, reply):
    actions["configure"](reply=reply)
    d = _invoke(actions).get_json()
    assert d["ok"] is False and d["status"] == "unknown" and "ok" in d["result"]["error"]


@pytest.mark.parametrize("error", [TimeoutError("timed out"), ConnectionResetError("reset"), ValueError("bad json")])
def test_a_lost_or_broken_response_is_recorded_as_unknown(actions, error):
    actions["configure"](error=error)
    d = _invoke(actions).get_json()
    assert d["status"] == "unknown" and "核对业务系统" in d["result"]["summary"]
    assert actions["log"]()[0]["real_writeback"] is None                 # 结果未知时,是否写回同样未知,不能记成未写回


def test_a_call_that_never_got_a_receipt_shows_up_as_unknown_after_a_restart(actions, monkeypatch):
    import time as _time
    actions["configure"]()
    stuck = {"id": "stuck1", "ts": "2026-11-15 09:00", "action_id": "act_trial", "action_cn": "插单试算", "object": "订单", "risk": "low",
             "operator": "计划员", "params": {}, "status": "executing", "execution_mode": "executor", "real_writeback": False,
             "effects": [], "approver": "", "approve_comment": "", "exec_started": _time.time() - bp_actions.STALE_AFTER - 5}
    fresh = dict(stuck, id="fresh1", exec_started=_time.time())
    bp_actions._atomic_json(bp_actions.ACTION_LOG_F, [stuck, fresh])
    d = actions["client"].get("/api/actions").get_json()
    assert d["unknown"] == 1 and d["executing"] == 1
    by_id = {r["id"]: r for r in actions["log"]()}
    assert by_id["stuck1"]["status"] == "unknown" and by_id["fresh1"]["status"] == "executing"


def test_an_approved_high_risk_action_is_executing_until_its_receipt_arrives(actions):
    seen = []
    actions["configure"](reply={"ok": True, "summary": "已发布", "real_writeback": True},
                         during=lambda: seen.append(actions["log"]()[0]["status"]))
    d = _invoke(actions, "act_release", {}).get_json()
    r = actions["client"].post("/api/action/approve", json={"id": d["id"], "decision": "approve", "approver": "主管"}).get_json()
    assert seen == ["executing"] and r["status"] == "executed"
    actions["configure"](reply={"summary": "回执不完整"})
    d2 = _invoke(actions, "act_release", {}).get_json()
    r2 = actions["client"].post("/api/action/approve", json={"id": d2["id"], "decision": "approve", "approver": "主管"}).get_json()
    assert r2["ok"] is False and r2["status"] == "unknown"


def test_an_unknown_record_is_settled_by_a_named_person_with_a_reason(actions):
    actions["configure"](error=TimeoutError("timed out"))
    d = _invoke(actions).get_json()
    c = actions["client"]
    assert c.post("/api/action/resolve", json={"id": d["id"], "outcome": "executed", "operator": "计划员"}).status_code == 400
    assert c.post("/api/action/resolve", json={"id": d["id"], "outcome": "maybe", "operator": "x", "comment": "y"}).status_code == 400
    r = c.post("/api/action/resolve", json={"id": d["id"], "outcome": "failed", "operator": "计划员", "comment": "排程系统里没有这次试算"})
    assert r.get_json() == {"ok": True, "status": "failed"}
    rec = actions["log"]()[0]
    assert rec["resolved_by"] == "计划员" and rec["resolve_comment"] == "排程系统里没有这次试算"
    assert rec["real_writeback"] is False                               # 核对后确认未执行:未写回
    assert c.post("/api/action/resolve", json={"id": d["id"], "outcome": "failed", "operator": "x", "comment": "y"}).status_code == 400


# ── 只读试算(DR-125) ───────────────────────────────────────────────

SIM = {"act_trial": {"url": "http://127.0.0.1:8096/trial", "simulate_only": True}, "act_release": "http://127.0.0.1:8096/release"}


def test_simulation_only_reaches_executors_marked_simulate_only(actions):
    actions["configure"](conf=SIM)
    code, d = bp_actions.invoke("act_trial", {"product": "T-100"}, "计划员", simulate=True)
    assert code == 200 and d["status"] == "executed" and d["execution_mode"] == "simulate"
    assert actions["calls"][0]["body"]["mode"] == "simulate"
    actions["configure"]()                                                    # 同一个动作,执行器没有标只做试算
    actions["calls"].clear()
    before = len(actions["log"]())
    code, d = bp_actions.invoke("act_trial", {"product": "T-100"}, "计划员", simulate=True)
    assert code == 403 and d["code"] == "simulate_not_allowed"
    assert actions["calls"] == [] and len(actions["log"]()) == before          # 没有调用,也没有留下记录


def test_simulation_refuses_high_risk_actions_and_actions_without_executors(actions, monkeypatch):
    actions["configure"](conf={"act_release": {"url": "http://127.0.0.1:8096/release", "simulate_only": True}})
    assert bp_actions.invoke("act_release", {}, "计划员", simulate=True)[0] == 403
    assert bp_actions.invoke("act_trial", {"product": "T-100"}, "计划员", simulate=True)[1]["code"] == "no_executor"
    assert actions["calls"] == [] and actions["log"]() == []


def test_a_simulation_that_claims_to_have_written_back_is_not_a_success(actions):
    actions["configure"](conf=SIM, reply={"ok": True, "summary": "已写入", "real_writeback": True})
    code, d = bp_actions.invoke("act_trial", {"product": "T-100"}, "计划员", simulate=True)
    assert d["ok"] is False and d["status"] == "failed" and "写回" in d["result"]["violation"]


def test_executor_calls_do_not_follow_redirects_or_use_environment_proxies(monkeypatch):
    import urllib.request
    handlers = {type(h).__name__ for h in bp_actions._OPENER.handlers}
    assert "_NoRedirect" in handlers
    proxies = [h for h in bp_actions._OPENER.handlers if isinstance(h, urllib.request.ProxyHandler)]
    assert all(h.proxies == {} for h in proxies)               # 没有按环境变量建立的代理处理器
    req = urllib.request.Request("http://127.0.0.1:8096/trial")
    with pytest.raises(urllib.error.HTTPError):
        bp_actions._NoRedirect().redirect_request(req, None, 302, "Found", {}, "http://example.com/elsewhere")


def test_the_action_centre_offers_to_resolve_an_unknown_outcome():
    ui = (Path(__file__).resolve().parents[2] / "ui/index.html").read_text(encoding="utf-8")
    assert "x.status==='unknown'" in ui and "acResolve(" in ui and "'/api/action/resolve'" in ui
    assert "核对依据(必填)" in ui and "系统不会重新发起" in ui
