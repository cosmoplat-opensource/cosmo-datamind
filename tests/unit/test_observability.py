# -*- coding: utf-8 -*-
"""ODD:构建与问数的结构化追踪和指标(DR-066)。

观测要可用,也要安全:敏感属性只留指纹,字符串去除地址,写盘失败不影响业务。
"""
import json

import pytest

import observability as obs


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("DATAMIND_OBS_DIR", str(tmp_path))
    obs.reset()
    yield
    obs.reset()


def _events():
    return obs.recent(limit=1000)


def test_run_records_start_steps_and_finish_with_durations():
    with obs.run("build", skills=3) as r:
        r.step("intake", True)
        r.step("llm_extract", False)
        r.finish("ok", objects=5)
    ev = [e for e in _events() if e["run"] == r.id]
    assert [e["event"] for e in ev] == ["start", "step", "step", "finish"]
    assert ev[-1]["objects"] == 5 and ev[-1]["steps"] == 2 and ev[-1]["failed_steps"] == 1
    assert all(isinstance(e["ms"], float) for e in ev if e["event"] == "step")
    names = {(c["name"], tuple(sorted(c["labels"].items()))) for c in obs.snapshot()["counters"]}
    assert ("build.runs", ()) in names and ("build.step_failed", (("step", "llm_extract"),)) in names


def test_sensitive_attributes_are_fingerprinted():
    r = obs.Run("qa", q="上个月华东各工位的不良率是多少", sql="SELECT * FROM secret_table", password="p@ss")
    r.finish("ok")
    dumped = json.dumps(_events(), ensure_ascii=False)
    assert "上个月华东" not in dumped and "secret_table" not in dumped and "p@ss" not in dumped
    start = next(e for e in _events() if e["event"] == "start")
    assert start["q_fp"]["len"] == len("上个月华东各工位的不良率是多少") and len(start["q_fp"]["sha"]) == 12


def test_strings_are_scrubbed_and_non_scalars_dropped():
    r = obs.Run("build", note="连接 jdbc:mysql://10.1.2.3:3306/x 失败", blob={"a": 1})
    r.finish("ok")
    start = next(e for e in _events() if e["event"] == "start")
    assert "10.1.2.3" not in start["note"] and "blob" not in start


def test_exception_inside_run_is_recorded_and_reraised():
    with pytest.raises(ValueError):
        with obs.run("qa"):
            raise ValueError("x")
    assert any(e["event"] == "finish" and e["status"] == "error" for e in _events())


def test_write_failure_never_breaks_the_caller(monkeypatch):
    monkeypatch.setenv("DATAMIND_OBS_DIR", "/dev/null/not-a-dir")
    obs.Run("build").step("intake", True)                   # 不抛异常


def test_snapshot_percentiles():
    for v in range(1, 101):
        obs.observe("qa.total_ms", v)
    h = next(x for x in obs.snapshot()["histograms"] if x["name"] == "qa.total_ms")
    assert h["count"] == 100 and h["p50"] in (50.0, 51.0) and h["p95"] in (95.0, 96.0) and h["max"] == 100.0


def test_obs_endpoints_contract():
    server = pytest.importorskip("server")
    obs.count("build.fallback")
    c = server.app.test_client()
    m = c.get("/api/obs/metrics").get_json()
    assert any(x["name"] == "build.fallback" for x in m["counters"])
    assert c.get("/api/obs/runs?kind=other").status_code == 400
    assert c.get("/api/obs/runs?limit=x").status_code == 400
    assert isinstance(c.get("/api/obs/runs?kind=build&limit=5").get_json(), list)


def test_gate_block_reasons_are_categorised():
    server = pytest.importorskip("server")
    assert server._gate_category("wip.zone_sn=area.zone_id 的编号按 factory_code 分租户复用,须同时按 factory_code 关联") == "tenant_scope"
    assert server._gate_category("device_type 有已删除行(按 is_del 软删除)") == "soft_delete"
    assert server._gate_category("JOIN 键 a.x=b.y 不在本体已验证关系上(关联未经数据验证,已拦截)") == "join_not_verified"
    assert server._gate_category("表 foo 不在本体/数据目录中(疑似臆造表名)") == "unknown_table"


def test_untimed_steps_do_not_pollute_duration_metrics():
    r = obs.Run("qa", mode="sync")
    r.step("exec_sql", True, timed=False)
    r.finish("ok")
    assert not any(h["name"] == "qa.step_ms" for h in obs.snapshot()["histograms"])
    step = next(e for e in _events() if e["event"] == "step")
    assert step["ms"] is None and step["step"] == "exec_sql"


def test_list_items_are_scrubbed_like_strings():
    r = obs.Run("build", hosts=["https://db.example.internal:8443/api", "10.9.8.7"])
    r.finish("ok")
    start = next(e for e in _events() if e["event"] == "start")
    assert "example.internal" not in json.dumps(start) and "10.9.8.7" not in json.dumps(start)


def test_closed_generator_inside_run_is_recorded_as_aborted():
    def gen():
        with obs.run("qa"):
            yield 1
            yield 2
    g = gen()
    next(g)
    g.close()
    assert any(e["event"] == "finish" and e["status"] == "aborted" for e in _events())
