# -*- coding: utf-8 -*-
"""构建作业由服务端持有(DR-101)。此前构建在流式响应的生成器里执行:浏览器关掉、MCP 进程退出、
代理断开,构建随之中断,作业号也只存在于发起方进程里。"""
import json
import threading
import time

import pytest

import server
from tests.scenarios import bench, fixtures


@pytest.fixture
def source(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    fx = fixtures.business(str(tmp_path / "business.db"))
    with bench._patched(server, WORK=str(work), _BUILD_CONN_F=str(work / "build_connections.json"),
                        _build_intent=lambda *a, **k: {}, _build_summary=lambda *a, **k: "摘要",
                        _llm_semantic_review=lambda *a, **k: None,
                        _llm_extract_ontology=lambda *a, **k: json.loads(json.dumps(fx["proposal"]))):
        yield bench._register(server, work, fx["db"], "业务回归库")


def _body(conn):
    return {"q": "构建计量本体", "source": conn, "name": "作业测试", "skills": bench.SKILLS}


def _poll(job_id, after=0, timeout=60):
    client, events, deadline = server.app.test_client(), [], time.time() + timeout
    while time.time() < deadline:
        d = client.get(f"/api/build/jobs/{job_id}?after={after}&wait=2").get_json()
        events += d["events"]; after = d["next"]
        if d["state"] != "running":
            return d, events
    raise AssertionError("构建作业未在限时内结束")


def test_submitted_job_returns_at_once_and_finishes_on_the_server(source):
    r = server.app.test_client().post("/api/build/jobs", json=_body(source))
    d = r.get_json()
    assert r.status_code == 202 and d["job_id"].startswith("bj_") and d["state"] == "running"
    final, events = _poll(d["job_id"])
    assert final["state"] == "done" and final["done"]["graph_key"].startswith("built_")
    assert events[-1]["type"] == "done" and any(e.get("step") == "critic" for e in events)
    assert server.load_ir(final["done"]["graph_key"])["relations"]


def test_invalid_request_is_rejected_without_creating_a_job(source):
    r = server.app.test_client().post("/api/build/jobs", json=dict(_body(source), base_graph="demo"))
    assert r.status_code == 400 and "job_id" not in r.get_json()


def test_build_continues_after_the_stream_is_dropped(source):
    r = server.app.test_client().post("/api/build/inquire", json=_body(source), buffered=False)
    job_id = r.headers["X-Build-Job"]
    first = next(iter(r.response))
    assert first.startswith(b"data: ")
    r.close()                                            # 浏览器关掉 / MCP 进程退出
    final, _events = _poll(job_id)
    assert final["state"] == "done"
    assert server.load_ir(final["done"]["graph_key"]) is not None


def test_a_late_subscriber_sees_the_whole_run_and_can_resume_from_an_offset(source):
    job_id = server.app.test_client().post("/api/build/jobs", json=_body(source)).get_json()["job_id"]
    final, events = _poll(job_id)
    again = server.app.test_client().get(f"/api/build/jobs/{job_id}").get_json()
    assert again["events"] == events and again["next"] == len(events)
    tail = server.app.test_client().get(f"/api/build/jobs/{job_id}?after={len(events) - 1}").get_json()
    assert [e["type"] for e in tail["events"]] == ["done"]


def test_unknown_job_is_not_found():
    assert server.app.test_client().get("/api/build/jobs/bj_missing").status_code == 404


def test_a_crash_in_the_build_ends_the_job_with_an_error_event():
    def boom():
        yield 'data: {"type": "step", "step": "intake", "ok": true}\n\n'
        raise RuntimeError("disk full")
    job = server._build_job_start(boom)
    job["thread"].join(5)
    assert job["state"] == "error"
    assert [e["type"] for e in job["events"]] == ["step", "error"] and "RuntimeError" in job["events"][-1]["error"]


def test_a_stream_that_ends_without_done_is_an_error_not_success():
    job = server._build_job_start(lambda: iter(['data: {"type": "step", "step": "intake", "ok": true}\n\n']))
    job["thread"].join(5)
    assert job["state"] == "error" and job["events"][-1]["type"] == "error"


def test_followers_get_a_heartbeat_while_a_step_is_silent():
    gate = threading.Event()

    def slow():
        yield 'data: {"type": "step", "step": "intake", "ok": true}\n\n'
        gate.wait(5)
        yield 'data: {"type": "done", "graph_key": "built_x"}\n\n'
    job = server._build_job_start(slow)
    got = []
    for chunk in server._build_job_follow(job, heartbeat=0.05):
        got.append(json.loads(chunk[6:]))
        if got[-1]["type"] == "tick":
            gate.set()
    assert [e["type"] for e in got if e["type"] != "tick"] == ["step", "done"]
    assert any(e["type"] == "tick" for e in got)


# ── 作业落盘、重启后可查、取消、发起方留痕(DR-103) ─────────────────────

def _forget_jobs():
    """模拟服务重启:进程内的作业登记清空,磁盘上的记录还在。"""
    with server._BUILD_JOBS_LOCK:
        server._BUILD_JOBS.clear()


def test_a_finished_job_can_still_be_read_after_a_restart(source):
    job_id = server.app.test_client().post("/api/build/jobs", json=_body(source)).get_json()["job_id"]
    final, events = _poll(job_id)
    _forget_jobs()
    again = server.app.test_client().get(f"/api/build/jobs/{job_id}").get_json()
    assert again["state"] == "done" and again["events"] == events
    assert again["done"]["graph_key"] == final["done"]["graph_key"]
    assert again["request"]["body"]["q"] == "构建计量本体"            # 留有原请求,可据此重新发起


def test_a_job_that_was_running_at_restart_is_reported_as_interrupted(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "WORK", str(tmp_path))
    gate = threading.Event()

    def slow():
        yield 'data: {"type": "step", "step": "intake", "ok": true}\n\n'
        gate.wait(10)
    job = server._build_job_start(slow)
    time.sleep(0.2)                                       # 首条事件已落盘
    _forget_jobs()
    d = server.app.test_client().get(f"/api/build/jobs/{job['id']}").get_json()
    assert d["state"] == "interrupted" and "重启" in d["error"]
    assert [e["type"] for e in d["events"]] == ["step", "error"]
    assert server.app.test_client().get(f"/api/build/jobs/{job['id']}").get_json()["state"] == "interrupted"
    gate.set()


def test_cancel_stops_the_build_and_runs_its_cleanup(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "WORK", str(tmp_path))
    proceed, closed = threading.Event(), threading.Event()

    def stream():
        try:
            yield 'data: {"type": "step", "step": "intake", "ok": true}\n\n'
            proceed.wait(10)
            yield 'data: {"type": "step", "step": "llm_extract", "ok": true}\n\n'
            yield 'data: {"type": "done", "graph_key": "built_never"}\n\n'
        finally:
            closed.set()
    job = server._build_job_start(stream)
    client = server.app.test_client()
    r = client.post(f"/api/build/jobs/{job['id']}/cancel")
    assert r.status_code == 202 and r.get_json()["cancel_requested"] is True
    proceed.set()                                         # 取消在下一步之间生效
    job["thread"].join(5)
    d = client.get(f"/api/build/jobs/{job['id']}").get_json()
    assert d["state"] == "cancelled" and d["done"] is None and closed.is_set()
    assert d["events"][-1].get("cancelled") is True
    assert client.post(f"/api/build/jobs/{job['id']}/cancel").status_code == 409      # 已结束的作业不能再取消


def test_cancel_and_lookup_reject_unknown_or_malformed_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "WORK", str(tmp_path))
    client = server.app.test_client()
    assert client.post("/api/build/jobs/bj_0000000000/cancel").status_code == 404
    for bad in ("..%2Fengine_config", "bj_../../x", "bj_ZZZ"):
        assert client.get("/api/build/jobs/" + bad).status_code == 404


def test_who_started_a_build_is_recorded_on_the_job_and_in_the_graph(source):
    r = server.app.test_client().post("/api/build/jobs", json=_body(source), headers={
        "X-DataMind-Actor": "ath:agent-7:analyst", "X-Request-Id": "req-123"})
    final, _events = _poll(r.get_json()["job_id"])
    assert final["request"]["actor"] == "ath:agent-7:analyst" and final["request"]["request_id"] == "req-123"
    key = final["done"]["graph_key"]
    started = server.load_ir(key)["build_manifest"]["initiated_by"]
    assert started == {"actor": "ath:agent-7:analyst", "request_id": "req-123", "job_id": final["job_id"]}
    audit = server.app.test_client().get("/api/ont/audit/" + key).get_json()
    assert audit["builds"][-1]["initiated_by"]["actor"] == "ath:agent-7:analyst"
