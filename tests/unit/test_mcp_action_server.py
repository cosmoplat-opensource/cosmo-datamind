# -*- coding: utf-8 -*-
"""动作层 MCP server 的协议合规与治理边界(DR-016)。

这层是外部 Agent 进入本系统的唯一入口,此前无单测,协议细节全靠手工验证。
两类断言:
  1. JSON-RPC 2.0 / MCP 生命周期规范——协商、通知不应答、未知方法错误码、
     畸形输入不得终止服务(stdio 服务器崩一次,整个客户端会话就断了);
  2. 治理边界——approve/deny 永不作为工具暴露,否则 Agent 就能自己批准自己
     提出的高风险动作,人审形同虚设。
"""
import json

import pytest

mcp = pytest.importorskip("mcp_action_server")


def _dispatch(lines):
    """把若干行输入喂给协议循环,收集写回 stdout 的应答。

    直接驱动 main() 而非起子进程:测的是协议分支,不是进程启动。
    """
    import io
    import sys
    out = io.StringIO()
    stdin, stdout = sys.stdin, sys.stdout
    sys.stdin, sys.stdout = io.StringIO("\n".join(lines) + "\n"), out
    try:
        mcp.main()
    finally:
        sys.stdin, sys.stdout = stdin, stdout
    return [json.loads(x) for x in out.getvalue().splitlines() if x.strip()]


def _init(version=None):
    params = {} if version is None else {"protocolVersion": version}
    return json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": params})


class TestProtocolNegotiation:
    @pytest.mark.parametrize("want", mcp.SUPPORTED_PROTOS)
    def test_supported_version_is_honored(self, want):
        """客户端请求的版本在支持列表内就沿用它——这是规范要求的协商结果。"""
        r = _dispatch([_init(want)])[0]["result"]
        assert r["protocolVersion"] == want

    def test_unknown_version_falls_back_to_newest_supported(self):
        """未知版本不得回显:回显等于声称支持任意版本,客户端据此调用即行为未定义。"""
        r = _dispatch([_init("2099-01-01")])[0]["result"]
        assert r["protocolVersion"] == mcp.SUPPORTED_PROTOS[0]

    def test_missing_version_falls_back(self):
        r = _dispatch([_init(None)])[0]["result"]
        assert r["protocolVersion"] == mcp.SUPPORTED_PROTOS[0]

    def test_server_info_and_capabilities(self):
        r = _dispatch([_init("2025-06-18")])[0]["result"]
        assert r["serverInfo"]["name"] == "datamind-actions"
        assert "tools" in r["capabilities"]


class TestLifecycleRobustness:
    def test_notification_gets_no_reply(self):
        """JSON-RPC:无 id 的是通知,不得应答——多一条应答会让客户端错配请求。"""
        assert _dispatch(['{"jsonrpc":"2.0","method":"notifications/initialized"}']) == []

    def test_unknown_method_returns_method_not_found(self):
        e = _dispatch(['{"jsonrpc":"2.0","id":9,"method":"nope/x"}'])[0]["error"]
        assert e["code"] == -32601

    def test_malformed_lines_do_not_kill_the_loop(self):
        """stdio 服务器崩一次整个会话就断了;一行畸形输入后仍须服务后续请求。"""
        out = _dispatch(['[1,2,3]', 'not json at all', '{"jsonrpc":"2.0"}',
                         '{"jsonrpc":"2.0","id":10,"method":"ping"}'])
        assert len(out) == 1 and out[0]["id"] == 10 and out[0]["result"] == {}


class TestToolDeclarations:
    def test_every_tool_is_fully_declared(self):
        """name/title/description/inputSchema 齐备:缺一样,客户端就得靠猜来用它。"""
        for t in mcp.TOOLS:
            assert t["name"] and t["title"] and t["description"]
            assert t["inputSchema"]["type"] == "object"
            assert t["inputSchema"].get("additionalProperties") is False

    def test_required_params_are_declared_in_schema(self):
        by = {t["name"]: t for t in mcp.TOOLS}
        assert set(by["invoke_action"]["inputSchema"]["required"]) == {
            "action_id", "params", "operator"}
        assert by["get_action_status"]["inputSchema"]["required"] == ["id"]

    def test_read_only_tools_are_annotated_as_such(self):
        """注解让客户端能做审批分流:读工具免打扰,写工具才需要提示用户。"""
        by = {t["name"]: t["annotations"] for t in mcp.TOOLS}
        assert by["list_actions"]["readOnlyHint"] is True
        assert by["get_action_status"]["readOnlyHint"] is True
        assert by["invoke_action"]["readOnlyHint"] is False

    def test_invoke_is_non_destructive_and_non_idempotent(self):
        """发起动作只追加记录(非破坏),但重复调用会产生两条记录(非幂等)。"""
        a = next(t["annotations"] for t in mcp.TOOLS if t["name"] == "invoke_action")
        assert a["destructiveHint"] is False and a["idempotentHint"] is False


class TestGovernanceBoundary:
    """治理边界:Agent 只能提议改变,不能批准改变。"""

    def test_approval_is_never_exposed_as_a_tool(self):
        names = {t["name"] for t in mcp.TOOLS}
        assert not (names & {"approve_action", "deny_action", "approve", "deny"})
        assert names == {"list_actions", "invoke_action", "get_action_status"}

    def test_unknown_tool_is_rejected(self):
        text, is_err = mcp.call_tool("approve_action", {"id": "x"})
        assert is_err and "未知工具" in text


class TestErrorSanitization:
    def test_transport_failure_reports_type_not_details(self, monkeypatch):
        """异常消息常含主机名/端口/证书路径;协议通道只给类型,现场留在 stderr。

        夹具里的地址取自 RFC 5737 的文档保留段 192.0.2.0/24,不要换成真实内网
        地址——本仓是开源仓,测试夹具同样会被公开。
        """
        def boom(*a, **k):
            raise ConnectionRefusedError("connect to 192.0.2.10:8092 refused")
        monkeypatch.setattr(mcp.urllib.request, "urlopen", boom)
        monkeypatch.setattr(mcp, "_log", lambda *a: None)
        _, err = mcp._http("GET", "/api/actions")
        assert "192.0.2.10" not in err and "ConnectionRefusedError" in err


class TestLogSanitization:
    def test_control_characters_are_stripped(self, capsys):
        """CWE-117:BASE 与工具名都可能带换行,不净化就能伪造一条日志记录。"""
        mcp._log("正常\n伪造的第二行\r\x1b[2J")
        err = capsys.readouterr().err
        assert err.count("\n") == 1 and "␊" in err


class TestCallToolRendering:
    """工具回文是模型唯一能读到的东西:参数名、风险级、审批状态必须原样传达。

    渲染错了不会报错,只会让外部 Agent 拿错参数名反复调用,或把 pending 当成已执行。
    """

    @pytest.fixture
    def types_payload(self):
        return {"types": [
            {"id": "dispatch", "cn": "派工", "object": "工单表", "risk": "high",
             "desc": "把检查项派给产线", "params": [
                 {"name": "line", "cn": "产线", "required": True},
                 {"name": "level", "cn": "级别", "options": ["A", "B"]}]},
            {"id": "note", "cn": "记录备注", "object": "-", "risk": "low",
             "desc": "留痕", "params": []}],
            "pending": 2, "executed": 40}

    def test_list_actions_exposes_param_names_and_risk(self, monkeypatch, types_payload):
        monkeypatch.setattr(mcp, "_http", lambda *a, **k: (types_payload, None))
        text, err = mcp.call_tool("list_actions", {})
        assert not err
        assert "id=dispatch" in text and "line(产线,必填)" in text
        assert "可选值:A/B" in text                      # 枚举值要给出,否则只能猜
        assert "高风险,须人审批" in text and "低风险,直执行" in text
        assert "待人批 2" in text and "已执行 40" in text

    def test_invoke_pending_says_agent_cannot_bypass(self, monkeypatch):
        """高风险回文必须写明「须由人批准」——模型据此决定是等待还是改走人工。"""
        monkeypatch.setattr(mcp, "_http", lambda *a, **k: ({"id": "a1", "status": "pending"}, None))
        text, err = mcp.call_tool("invoke_action",
                                  {"action_id": "dispatch", "params": {}, "operator": "张三"})
        assert not err and "id=a1" in text
        assert "pending" in text and "批准" in text

    def test_invoke_executed_reports_audit(self, monkeypatch):
        monkeypatch.setattr(mcp, "_http", lambda *a, **k: ({"id": "a2", "status": "executed"}, None))
        text, err = mcp.call_tool("invoke_action",
                                  {"action_id": "note", "params": {}, "operator": "张三"})
        assert not err and "executed" in text and "审计" in text

    def test_invoke_forwards_operator_and_params(self, monkeypatch):
        """operator 必须原样送达服务端——审计记录靠它认人。"""
        seen = {}
        def fake(method, path, payload=None):
            seen.update(method=method, path=path, payload=payload)
            return {"id": "x", "status": "executed"}, None
        monkeypatch.setattr(mcp, "_http", fake)
        mcp.call_tool("invoke_action", {"action_id": "d", "params": {"line": "一线"},
                                        "operator": "李四"})
        assert seen["method"] == "POST" and seen["path"] == "/api/action/invoke"
        assert seen["payload"] == {"action_id": "d", "params": {"line": "一线"}, "operator": "李四"}

    def test_invoke_missing_params_defaults_to_empty_object(self, monkeypatch):
        """漏传 params 时送空对象,让服务端的必填校验去报错,而不是在此处崩掉。"""
        seen = {}
        monkeypatch.setattr(mcp, "_http",
                            lambda m, p, payload=None: (seen.update(payload=payload),
                                                        ({"id": "x", "status": "executed"}, None))[1])
        mcp.call_tool("invoke_action", {"action_id": "d", "operator": "李四"})
        assert seen["payload"]["params"] == {}

    def test_status_renders_approval_and_effects(self, monkeypatch):
        log = {"items": [{"id": "a1", "action_cn": "派工", "status": "executed",
                          "operator": "张三", "ts": "2026-09-03 10:00",
                          "approver": "王五", "approve_comment": "同意",
                          "effects": ["工单已创建", "已通知"]}]}
        monkeypatch.setattr(mcp, "_http", lambda *a, **k: (log, None))
        text, err = mcp.call_tool("get_action_status", {"id": "a1"})
        assert not err
        assert "审批人:王五" in text and "同意" in text
        assert "工单已创建 / 已通知" in text

    def test_status_pending_tells_agent_to_wait(self, monkeypatch):
        log = {"items": [{"id": "a1", "action_cn": "派工", "status": "pending",
                          "operator": "张三", "ts": "t"}]}
        monkeypatch.setattr(mcp, "_http", lambda *a, **k: (log, None))
        text, _ = mcp.call_tool("get_action_status", {"id": "a1"})
        assert "Agent 无法批准" in text

    def test_status_unknown_id_is_an_error(self, monkeypatch):
        monkeypatch.setattr(mcp, "_http", lambda *a, **k: ({"items": []}, None))
        text, err = mcp.call_tool("get_action_status", {"id": "nope"})
        assert err and "未找到" in text

    @pytest.mark.parametrize("tool,args", [
        ("list_actions", {}),
        ("invoke_action", {"action_id": "d", "params": {}, "operator": "x"}),
        ("get_action_status", {"id": "a1"}),
    ])
    def test_upstream_error_is_surfaced_as_tool_error(self, monkeypatch, tool, args):
        """服务不可达时三个工具都要报错,不能假装成功回一段空文本。"""
        monkeypatch.setattr(mcp, "_http", lambda *a, **k: (None, "DataMind 服务不可达"))
        text, err = mcp.call_tool(tool, args)
        assert err and "不可达" in text


class TestBaseResolution:
    def test_wildcard_host_falls_back_to_loopback(self, monkeypatch):
        """服务端绑 0.0.0.0 时,客户端仍应走回环——0.0.0.0 不是可连接地址。"""
        monkeypatch.setenv("DATAMIND_HOST", "0.0.0.0")
        monkeypatch.setenv("DATAMIND_PORT", "9001")
        assert mcp._default_base() == "http://localhost:9001"

    def test_host_and_port_compose(self, monkeypatch):
        monkeypatch.setenv("DATAMIND_HOST", "example.internal")
        monkeypatch.setenv("DATAMIND_PORT", "8100")
        assert mcp._default_base() == "http://example.internal:8100"
