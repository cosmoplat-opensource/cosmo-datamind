# -*- coding: utf-8 -*-
"""可执行动作(DR-120):动作类型在部署配置里指定了本机执行器时,低风险在发起时调用,高风险在批准后调用;
没有配置时与此前一样只记录。执行器地址不能由请求方指定,也不能指向本机以外。"""
import io
import json
import urllib.error

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

    def configure(reply=None, error=None, conf=None):
        monkeypatch.setenv("DATAMIND_ACTION_EXECUTORS", json.dumps(conf if conf is not None else {
            "act_trial": "http://127.0.0.1:8096/trial", "act_release": "http://localhost:8096/release"}))

        def fake(req, timeout=None):
            calls.append({"url": req.full_url, "body": json.loads(req.data), "timeout": timeout})
            if error:
                raise error
            return _Resp(reply if reply is not None else {"ok": True, "summary": "可按期", "real_writeback": False})
        monkeypatch.setattr(bp_actions.urllib.request, "urlopen", fake)
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
    rec = actions["log"]()[0]
    assert rec["status"] == "executed" and rec["effects"] == ["试算 1 次"] and "executor_seconds" in rec


def test_an_executor_failure_is_recorded_not_raised(actions):
    actions["configure"](reply={"ok": False, "summary": "没有可用的干燥炉"})
    d = _invoke(actions).get_json()
    assert d["ok"] is False and d["status"] == "failed" and d["result"]["summary"] == "没有可用的干燥炉"
    actions["configure"](error=urllib.error.HTTPError("u", 422, "bad", {}, io.BytesIO(json.dumps({"summary": "参数不合法"}).encode())))
    d = _invoke(actions).get_json()
    assert d["status"] == "failed" and d["result"] == {"error": "HTTP 422", "summary": "参数不合法"}
    actions["configure"](error=ConnectionRefusedError("refused"))
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
