"""Regression coverage for decision-capture wording and malformed action inputs."""
import copy
import io
import json

from flask import Flask
import pytest

import bp_actions as actions
import mcp_action_server as mcp


def _dispatch(monkeypatch, messages):
    output = io.StringIO()
    monkeypatch.setattr(mcp.sys, "stdin", io.StringIO(
        "\n".join(json.dumps(message) for message in messages) + "\n"))
    monkeypatch.setattr(mcp.sys, "stdout", output)
    mcp.main()
    return [json.loads(line) for line in output.getvalue().splitlines()]


@pytest.mark.parametrize("method", ["initialize", "tools/call"])
@pytest.mark.parametrize("params", [[], [1], "bad", 1, False, None])
def test_non_object_rpc_params_do_not_end_the_session(monkeypatch, method, params):
    def unexpected(*args, **kwargs):
        pytest.fail("invalid request must not reach a tool or HTTP")

    monkeypatch.setattr(mcp, "call_tool", unexpected)
    replies = _dispatch(monkeypatch, [
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        {"jsonrpc": "2.0", "id": 2, "method": "ping"},
    ])
    assert replies[0]["error"]["code"] == -32602
    assert replies[1] == {"jsonrpc": "2.0", "id": 2, "result": {}}


@pytest.mark.parametrize("arguments", [[], [1], "bad", 1, False, None])
def test_non_object_tool_arguments_are_rejected_before_dispatch(monkeypatch, arguments):
    monkeypatch.setattr(mcp, "call_tool", lambda *args: pytest.fail("unexpected dispatch"))
    replies = _dispatch(monkeypatch, [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "invoke_action", "arguments": arguments}},
        {"jsonrpc": "2.0", "id": 2, "method": "ping"},
    ])
    assert replies[0]["error"]["code"] == -32602
    assert replies[1]["result"] == {}


def test_invalid_notification_is_silent_and_session_continues(monkeypatch):
    assert _dispatch(monkeypatch, [
        {"jsonrpc": "2.0", "method": "initialize", "params": [1]},
        {"jsonrpc": "2.0", "id": 2, "method": "ping"},
    ]) == [{"jsonrpc": "2.0", "id": 2, "result": {}}]


@pytest.mark.parametrize("arguments", [[], [1], "bad", 1, False, None])
def test_direct_tool_call_rejects_non_object_arguments(monkeypatch, arguments):
    monkeypatch.setattr(mcp, "_http", lambda *args: pytest.fail("unexpected HTTP"))
    text, error = mcp.call_tool("invoke_action", arguments)
    assert error and "对象" in text


@pytest.mark.parametrize("params", [[], [1], "bad", 1, False, None])
def test_mcp_invoke_rejects_non_object_action_params(monkeypatch, params):
    monkeypatch.setattr(mcp, "_http", lambda *args: pytest.fail("unexpected HTTP"))
    text, error = mcp.call_tool("invoke_action", {
        "action_id": "number", "operator": "tester", "params": params})
    assert error and "对象" in text


def test_action_tool_descriptions_state_decision_capture():
    for tool in mcp.TOOLS:
        if tool["name"] in {"list_actions", "invoke_action", "get_action_status"}:
            description = tool["description"]
            assert "决策记录" in description
            assert "写回" in description
            assert "立即执行" not in description
            assert "批准后才生效" not in description


@pytest.mark.parametrize("status", ["pending", "executed"])
def test_invoke_response_does_not_claim_business_execution(monkeypatch, status):
    monkeypatch.setattr(mcp, "_http", lambda *args: ({
        "id": "record", "status": status,
        "execution_mode": "decision_capture", "real_writeback": False}, None))
    text, error = mcp.call_tool("invoke_action", {
        "action_id": "number", "operator": "tester", "params": {"amount": 0}})
    assert not error and status in text
    assert "决策记录" in text and "未写回业务系统" in text
    assert "已执行" not in text and "才会执行" not in text


def test_list_and_status_identify_recorded_effects(monkeypatch):
    monkeypatch.setattr(mcp, "_http", lambda *args: ({
        "types": [{"id": "number", "cn": "金额", "risk": "low", "params": []}],
        "executed": 4, "pending": 1,
        "execution": {"mode": "decision_capture", "real_writeback": False}}, None))
    text, error = mcp.call_tool("list_actions", {})
    assert not error and "已登记 4" in text and "低风险,直接登记" in text
    assert "决策记录" in text and "未写回业务系统" in text
    monkeypatch.setattr(mcp, "_http", lambda *args: ({"items": [{
        "id": "record", "action_cn": "金额", "status": "executed",
        "operator": "tester", "ts": "2026-09-07", "effects": ["请求已记录"],
        "execution_mode": "decision_capture", "real_writeback": False}]}, None))
    text, error = mcp.call_tool("get_action_status", {"id": "record"})
    assert not error and "记录内容:" in text
    assert "决策记录" in text and "未写回业务系统" in text


@pytest.fixture
def action_client(monkeypatch):
    records = []
    types = [{"id": "number", "cn": "数值登记", "risk": "low", "enabled": True,
              "params": [{"name": "amount", "cn": "数值", "type": "number", "required": True}],
              "effects": [{"type": "append_event", "event": "数值已登记"}]}]
    monkeypatch.setattr(actions, "load_action_types", lambda: copy.deepcopy(types))
    monkeypatch.setattr(actions, "load_action_log", lambda: copy.deepcopy(records))

    def save(path, values):
        assert path == actions.ACTION_LOG_F
        records[:] = copy.deepcopy(values)

    monkeypatch.setattr(actions, "_atomic_json", save)
    app = Flask(__name__)
    app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    app.register_blueprint(actions.bp_actions)
    return app.test_client(), records


@pytest.mark.parametrize("params", [[], [1], "bad", 1, False, None])
def test_http_invoke_rejects_non_object_params_without_writing(action_client, params):
    client, records = action_client
    response = client.post("/api/action/invoke", json={
        "action_id": "number", "operator": "tester", "params": params})
    assert response.status_code == 400
    assert "对象" in response.get_json()["error"]
    assert records == []


@pytest.mark.parametrize("body", [[], [1], "bad", 1, False, None])
def test_http_invoke_rejects_non_object_body_without_writing(action_client, body):
    client, records = action_client
    response = client.post("/api/action/invoke", data=json.dumps(body), content_type="application/json")
    assert response.status_code == 400
    assert "对象" in response.get_json()["error"]
    assert records == []


@pytest.mark.parametrize("value,expected", [(0, "0"), (0.0, "0.0"), ("0", "0"),
                                           (-2.5, "-2.5"), (" 1e3 ", "1e3")])
def test_http_number_preserves_zero_and_finite_values(action_client, value, expected):
    client, records = action_client
    response = client.post("/api/action/invoke", json={
        "action_id": "number", "operator": "tester", "params": {"amount": value}})
    assert response.status_code == 200
    assert response.get_json()["execution_mode"] == "decision_capture"
    assert response.get_json()["real_writeback"] is False
    assert records[0]["params"]["amount"] == expected


@pytest.mark.parametrize("value", ["NaN", "nan", "Infinity", "+inf", "-Infinity", "1e309",
                                  float("nan"), float("inf"), float("-inf")])
def test_http_non_finite_numbers_are_rejected_without_writing(action_client, value):
    client, records = action_client
    response = client.post("/api/action/invoke", json={
        "action_id": "number", "operator": "tester", "params": {"amount": value}})
    assert response.status_code == 400
    assert "有限" in response.get_json()["error"]
    assert records == []


@pytest.mark.parametrize("value", [True, False, {}, [], "not-a-number"])
def test_http_invalid_numbers_are_rejected_without_writing(action_client, value):
    client, records = action_client
    response = client.post("/api/action/invoke", json={
        "action_id": "number", "operator": "tester", "params": {"amount": value}})
    assert response.status_code == 400
    assert records == []
