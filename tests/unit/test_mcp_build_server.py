# -*- coding: utf-8 -*-
"""本体构建 MCP server 的协议合规与治理边界(DR-058)。

与动作层 MCP(DR-016)同构的两类断言:
  1. JSON-RPC 2.0 / MCP 生命周期——协商、通知不应答、未知方法错误码、畸形输入不得终止服务;
  2. 治理边界——把关系置为 verified/asserted、把指标置为 certified、删除图谱
     都不得作为工具暴露,否则 Agent 就能改写只有数据裁决和人工评审才能产生的结论状态。

另有作业生命周期:start_build 立即返回、SSE 无 done 不算完成(DR-056 同规)。
"""
import json
import time

import pytest

mcp = pytest.importorskip("mcp_build_server")


def _dispatch(lines):
    """把若干行输入喂给协议循环,收集写回 stdout 的应答(不起子进程)。"""
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


@pytest.fixture(autouse=True)
def _clean_jobs():
    with mcp._JOBS_LOCK:
        mcp._JOBS.clear()
    yield
    with mcp._JOBS_LOCK:
        mcp._JOBS.clear()


class TestProtocolNegotiation:
    @pytest.mark.parametrize("want", mcp.SUPPORTED_PROTOS)
    def test_supported_version_is_honored(self, want):
        r = _dispatch([_init(want)])[0]["result"]
        assert r["protocolVersion"] == want

    def test_unknown_version_falls_back_to_newest_supported(self):
        r = _dispatch([_init("2099-01-01")])[0]["result"]
        assert r["protocolVersion"] == mcp.SUPPORTED_PROTOS[0]

    def test_server_info_names_build_server(self):
        r = _dispatch([_init("2025-06-18")])[0]["result"]
        assert r["serverInfo"]["name"] == "datamind-build"
        assert "tools" in r["capabilities"]

    def test_notification_gets_no_reply(self):
        assert _dispatch(['{"jsonrpc":"2.0","method":"notifications/initialized"}']) == []

    def test_unknown_method_returns_method_not_found(self):
        e = _dispatch(['{"jsonrpc":"2.0","id":9,"method":"nope/x"}'])[0]["error"]
        assert e["code"] == -32601

    def test_malformed_lines_do_not_kill_the_loop(self):
        out = _dispatch(['[1,2,3]', 'not json at all',
                         '{"jsonrpc":"2.0","id":10,"method":"ping"}'])
        assert len(out) == 1 and out[0]["id"] == 10 and out[0]["result"] == {}


class TestToolDeclarations:
    def test_every_tool_is_fully_declared(self):
        for t in mcp.TOOLS:
            assert t["name"] and t["title"] and t["description"]
            assert t["inputSchema"]["type"] == "object"
            assert t["inputSchema"].get("additionalProperties") is False

    def test_start_build_requires_q_only(self):
        by = {t["name"]: t for t in mcp.TOOLS}
        assert by["start_build"]["inputSchema"]["required"] == ["q"]
        assert by["get_build"]["inputSchema"]["required"] == ["job_id"]
        assert by["wait_build"]["inputSchema"]["required"] == ["job_id"]
        assert by["get_quality"]["inputSchema"]["required"] == ["graph"]
        assert by["review_queue"]["inputSchema"]["required"] == ["graph"]

    def test_only_start_build_is_a_write_tool(self):
        writers = {t["name"] for t in mcp.TOOLS if not t["annotations"].get("readOnlyHint")}
        assert writers == {"start_build"}
        a = next(t["annotations"] for t in mcp.TOOLS if t["name"] == "start_build")
        assert a["destructiveHint"] is False and a["idempotentHint"] is False


class TestGovernanceBoundary:
    """构建产物状态只由服务端数据裁决与人工评审产生;Agent 工具面不得绕过。"""

    def test_status_writing_tools_are_never_exposed(self):
        names = {t["name"] for t in mcp.TOOLS}
        banned = {"set_verified", "set_asserted", "set_certified", "approve", "deny",
                  "approve_action", "delete_graph", "review", "confirm_metric"}
        assert not (names & banned)
        assert names == {"list_build_sources", "list_build_skills", "list_built_graphs",
                         "start_build", "get_build", "wait_build", "get_quality", "review_queue"}

    def test_unknown_tool_is_rejected(self):
        text, is_err = mcp.call_tool("set_verified", {})
        assert is_err and "未知工具" in text


class TestBaseResolution:
    def test_wildcard_host_falls_back_to_loopback(self, monkeypatch):
        monkeypatch.setenv("DATAMIND_HOST", "0.0.0.0")
        monkeypatch.setenv("DATAMIND_PORT", "9001")
        assert mcp._default_base() == "http://localhost:9001"

    def test_host_and_port_compose(self, monkeypatch):
        monkeypatch.setenv("DATAMIND_HOST", "example.internal")
        monkeypatch.setenv("DATAMIND_PORT", "8100")
        assert mcp._default_base() == "http://example.internal:8100"


class TestStartBuild:
    def _fake_stream(self, monkeypatch, events, result_error=None):
        seen = {}

        def fake(path, payload, on_event, timeout=90):
            seen.update(path=path, payload=payload)
            for ev in events:
                on_event(ev)
            return len(events), result_error
        monkeypatch.setattr(mcp, "_http_stream", fake)
        return seen

    def _wait_done(self, job_id, timeout=5.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = mcp._JOBS.get(job_id)
            if job is not None and job["status"] != "running":
                return job
            time.sleep(0.02)
        return mcp._JOBS.get(job_id)

    def test_requires_q(self):
        text, err = mcp.call_tool("start_build", {})
        assert err and "q" in text

    def test_rejects_bad_source(self):
        text, err = mcp.call_tool("start_build", {"q": "建销售本体", "source": "../etc"})
        assert err and "source" in text

    def test_job_runs_and_reports_done(self, monkeypatch):
        seen = self._fake_stream(monkeypatch, [
            {"type": "step", "step": "intake", "ok": True, "info": "接收诉求"},
            {"type": "status", "text": "数据验证中"},
            {"type": "done", "graph_key": "built_ab12cd", "name": "销售本体",
             "method": "LLM 辅助提议", "summary": "覆盖订单与客户",
             "stats": {"objects": 9, "links": 10, "verified": 6},
             "quality": {"result": "review", "summary": {"blocking_issues": 0, "review_items": 3}}},
        ])
        text, err = mcp.call_tool("start_build",
                                  {"q": "建销售本体", "source": "demo",
                                   "skills": ["ontology-semi-auto"], "cqs": ["上月销售额多少?"]})
        assert not err and "job_id=" in text
        job_id = text.split("job_id=")[1].split("(")[0].strip()
        job = self._wait_done(job_id)
        assert job["status"] == "done" and job["graph_key"] == "built_ab12cd"
        assert job["stats"]["verified"] == 6
        assert seen["path"] == "/api/build/inquire"
        assert seen["payload"]["q"] == "建销售本体" and seen["payload"]["source"] == "demo"
        assert seen["payload"]["skills"] == ["ontology-semi-auto"]
        assert seen["payload"]["cqs"] == ["上月销售额多少?"]

    def test_stream_without_done_is_an_error_not_success(self, monkeypatch):
        """DR-056 同规:构建 SSE 必须有 done 才算完成;半程流不得冒充成功。"""
        self._fake_stream(monkeypatch, [
            {"type": "step", "step": "intake", "ok": True, "info": "接收诉求"},
            {"type": "log", "text": "抽取中"},
        ])
        text, _ = mcp.call_tool("start_build", {"q": "建本体"})
        job_id = text.split("job_id=")[1].split("(")[0].strip()
        job = self._wait_done(job_id)
        assert job["status"] == "error" and "done" in job["error"]

    def test_error_event_marks_job_failed(self, monkeypatch):
        self._fake_stream(monkeypatch, [{"type": "error", "error": "数据源为空"}])
        text, _ = mcp.call_tool("start_build", {"q": "建本体"})
        job_id = text.split("job_id=")[1].split("(")[0].strip()
        job = self._wait_done(job_id)
        assert job["status"] == "error" and "数据源为空" in job["error"]

    def test_get_build_unknown_job_is_an_error(self):
        text, err = mcp.call_tool("get_build", {"job_id": "jb_nope"})
        assert err and "未找到" in text

    def test_get_build_running_shows_progress(self, monkeypatch):
        """流未结束时作业保持 running,get_build 返回最近过程事件而非结论。"""
        import threading
        release = threading.Event()

        def blocking_stream(path, payload, on_event, timeout=90):
            on_event({"type": "step", "step": "intake", "ok": True, "info": "接收诉求"})
            release.wait(3.0)                    # 挂住流,作业停在 running
            return 1, None
        monkeypatch.setattr(mcp, "_http_stream", blocking_stream)
        text, _ = mcp.call_tool("start_build", {"q": "建本体"})
        job_id = text.split("job_id=")[1].split("(")[0].strip()
        time.sleep(0.1)
        text, err = mcp.call_tool("get_build", {"job_id": job_id})
        assert not err and "构建进行中" in text and "接收诉求" in text
        release.set()

    def test_wait_build_returns_when_finished(self, monkeypatch):
        self._fake_stream(monkeypatch, [{"type": "done", "graph_key": "built_x",
                                         "stats": {}, "quality": {}}])
        text, _ = mcp.call_tool("start_build", {"q": "建本体"})
        job_id = text.split("job_id=")[1].split("(")[0].strip()
        text, err = mcp.call_tool("wait_build", {"job_id": job_id, "timeout": 5})
        assert not err and "built_x" in text


class TestRenderers:
    """工具回文是模型唯一能读到的东西:参数名、状态、验收语义必须原样传达。"""

    def test_sources_renders_ids_and_readiness(self, monkeypatch):
        monkeypatch.setattr(mcp, "_http", lambda *a, **k: (
            {"sources": [{"id": "demo", "name": "示例主库", "kind": "sqlite", "tables": 108, "ready": True},
                         {"id": "uploads", "name": "上传数据库", "kind": "sqlite", "tables": 0, "ready": False}],
             "assets": [{"name": "说明.md", "usable": True}]}, None))
        text, err = mcp.call_tool("list_build_sources", {})
        assert not err and "id=demo" in text and "就绪" in text and "不可用" in text
        assert "说明.md" in text and "source" in text

    def test_skills_empty_is_honest_error(self, monkeypatch):
        monkeypatch.setattr(mcp, "_http", lambda *a, **k: ([], None))
        text, err = mcp.call_tool("list_build_skills", {})
        assert err and "暂无" in text

    def test_built_graphs_render_quality_and_hint(self, monkeypatch):
        monkeypatch.setattr(mcp, "_http", lambda *a, **k: (
            [{"key": "built_a1", "name": "销售本体", "objects": 9, "links": 10,
              "verified": 6, "quality_result": "review", "rounds": 2, "ts": "09-11 10:00"}], None))
        text, err = mcp.call_tool("list_built_graphs", {})
        assert not err and "built_a1" in text and "待复核" in text and "base_graph" in text

    def test_quality_renders_result_and_issues(self, monkeypatch):
        monkeypatch.setattr(mcp, "_http", lambda *a, **k: (
            {"result": "fail", "summary": {"blocking_issues": 1, "review_items": 2, "verified_checked": 6},
             "blocking_issues": [{"type": "verified_missing_join_key", "desc": "缺连接键", "fix": "重新取证"}],
             "review_queue": [{"type": "candidate_relations", "desc": "3 条候选"}],
             "cq": {"provided": False}}, None))
        text, err = mcp.call_tool("get_quality", {"graph": "built_a1"})
        assert not err and "不通过" in text and "缺连接键" in text and "重新取证" in text
        assert "未提供" in text and "不冒充已验收" in text

    def test_review_queue_renders_pending_and_human_boundary(self, monkeypatch):
        monkeypatch.setattr(mcp, "_http", lambda method, path, payload=None: (
            {"graph": "built_a1", "counts": {"total": 10, "pending": 2, "approved": 5,
                                             "rejected": 1, "disputed": 2, "obj_candidate": 1},
             "rows": [{"sn": "销售订单", "s": "fact_sales_order", "verb": "关联",
                       "tn": "客户", "t": "dim_customer", "status": "candidate",
                       "semantic": "pass", "overlap": 88.0, "pending": True},
                      {"sn": "工单", "s": "fact_wo", "verb": "关联", "tn": "客户",
                       "t": "dim_customer", "status": "verified", "semantic": "fail",
                       "overlap": 90.0, "pending": True}]}, None))
        text, err = mcp.call_tool("review_queue", {"graph": "built_a1"})
        assert not err
        assert "待人审 2" in text and "fact_sales_order" in text and "88.0" in text
        assert "由人在 DataMind" in text or "界面" in text

    def test_upstream_error_is_surfaced_as_tool_error(self, monkeypatch):
        monkeypatch.setattr(mcp, "_http", lambda *a, **k: (None, "DataMind 服务不可达"))
        for tool in ("list_build_sources", "list_build_skills", "list_built_graphs"):
            text, err = mcp.call_tool(tool, {})
            assert err and "不可达" in text


class TestLogSanitization:
    def test_control_characters_are_stripped(self, capsys):
        mcp._log("正常\n伪造的第二行\r\x1b[2J")
        err = capsys.readouterr().err
        assert err.count("\n") == 1 and "␊" in err
