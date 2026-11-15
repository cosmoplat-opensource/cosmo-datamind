# -*- coding: utf-8 -*-
"""正式问数的发布资格(DR-115)。

此前「构建完成」就等于可用:验收检查判为不通过的草案,同样能在正式问数里返回业务结果。
问数资格只看表是否可查,构建记录里的质量结论没有参与判断。"""
import json

import pytest

import ontology_release as R
import server

OBJS = [{"name": "客户", "table": "dim_customer", "kind": "object"}, {"name": "销售订单", "table": "fact_sales_order", "kind": "event"}]


def _graph(tmp_path, key, result, rounds=1, extra=()):
    ir = {"scenario": {"name": key}, "objects": OBJS + list(extra), "relations": [],
          "build_quality": {"result": result, "summary": {"blocking_issues": 3 if result == "fail" else 0}},
          "build_history": [{"round": i + 1} for i in range(rounds)]}
    (tmp_path / f"{key}.json").write_text(json.dumps(ir, ensure_ascii=False), encoding="utf-8")
    return key


@pytest.fixture
def work(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "WORK", str(tmp_path))
    monkeypatch.setattr(server, "agent_sql_plan", lambda *a, **k: None)
    monkeypatch.setattr(server, "_match_qa_skill", lambda _q: None)
    monkeypatch.setattr(server, "narrative_llm", lambda *a, **k: "ok")
    monkeypatch.delenv("DATAMIND_QA_REQUIRE_PUBLISHED", raising=False)
    server._QA_CACHE.clear()
    return tmp_path


def _ask(graph, **extra):
    headers = extra.pop("headers", None)
    return server.app.test_client().post("/api/chat", json={"q": "各客户的销售订单金额排名", "graphs": [graph], **extra}, headers=headers)


# ── 判定规则(不依赖 Web 层) ────────────────────────────────────────

def test_state_rules():
    passed = {"build_quality": {"result": "pass"}, "objects": [{"name": "a"}]}
    rev = R.revision_of(passed)
    assert R.state({"build_quality": {"result": "fail"}}, None)["state"] == "blocked"
    assert R.state({"build_quality": {"result": "review"}}, None)["state"] == "draft"
    assert R.state({"objects": []}, None)["state"] == "draft"                       # 内置与早期产物没有质量记录
    rec = {"state": "published", "revision": rev}
    assert R.state(passed, rec)["state"] == "published" and R.state(passed, rec)["revision"] == rev
    changed = {**passed, "objects": [{"name": "a"}, {"name": "b"}]}
    assert R.state(changed, rec)["state"] == "draft"                                # 发布之后内容变了
    assert R.state({"build_quality": {"result": "review"}}, None, strict=True)["usable"] is False
    assert R.state(passed, rec, strict=True)["usable"] is True
    assert R.state(passed, {"state": "published", "round": 2})["state"] == "draft"  # 旧格式的发布记录没有修订号,视为失效


def test_the_revision_follows_content_not_the_length_of_the_build_history():
    base = {"build_quality": {"result": "pass"}, "objects": [{"name": "a"}], "build_history": list(range(50))}
    rec = {"state": "published", "revision": R.revision_of(base)}
    more = {**base, "objects": [{"name": "a"}, {"name": "b"}], "build_history": list(range(1, 51))}   # 历史已到上限,条数不变
    assert R.state(more, rec, strict=True)["state"] == "draft" and R.state(more, rec, strict=True)["usable"] is False
    same = {**base, "build_history": list(range(7)), "scenario": {"iterations": 9}}                    # 只有过程记录变了
    assert R.state(same, rec)["state"] == "published"


def test_a_withdrawn_graph_is_blocked_in_every_mode():
    ir = {"build_quality": {"result": "pass"}, "objects": [{"name": "a"}]}
    for strict in (False, True):
        st = R.state(ir, {"state": "withdrawn", "revision": R.revision_of(ir), "by": "发布人"}, strict=strict)
        assert st["state"] == "blocked" and st["usable"] is False and "撤回" in st["reason"] and st["by"] == "发布人"
    newer = {**ir, "objects": [{"name": "a"}, {"name": "b"}]}                        # 撤回之后又构建出新内容:是新的草案
    assert R.state(newer, {"state": "withdrawn", "revision": R.revision_of(ir)})["state"] == "draft"


# ── 正式问数 ────────────────────────────────────────────────────────

def test_a_draft_that_failed_the_quality_check_is_refused(work):
    r = _ask(_graph(work, "built_failed1", "fail"))
    assert r.status_code == 409 and r.get_json()["code"] == "graph_not_released"
    assert "built_failed1" in r.get_json()["graphs"] and "验收" in r.get_json()["error"]


def test_the_streaming_and_diagnosis_entries_apply_the_same_rule(work):
    key = _graph(work, "built_failed2", "fail")
    c = server.app.test_client()
    for path in ("/api/chat/stream", "/api/diagnose/stream"):
        r = c.post(path, json={"q": "各客户的销售订单金额排名", "graphs": [key]})
        assert r.status_code == 409 and r.get_json()["code"] == "graph_not_released", path


def test_a_draft_awaiting_review_is_usable_and_labelled(work):
    r = _ask(_graph(work, "built_review1", "review"))
    assert r.status_code == 200 and r.headers["X-DataMind-Graph-Release"] == "draft"


def test_a_failed_draft_can_be_previewed_by_a_person_but_not_through_an_agent(work):
    key = _graph(work, "built_failed3", "fail")
    r = _ask(key, preview=True)
    assert r.status_code == 200 and r.headers["X-DataMind-Graph-Release"] == "preview"
    agent = _ask(key, preview=True, headers={"X-DataMind-Actor": "ath:agent-7:analyst"})
    assert agent.status_code == 409


def test_a_cached_answer_does_not_bypass_the_rule(work):
    key = _graph(work, "built_flip1", "review")
    assert _ask(key).status_code == 200
    _graph(work, key, "fail")                                   # 之后的一轮构建被判不通过
    assert _ask(key).status_code == 409


# ── 发布与撤回 ──────────────────────────────────────────────────────

def _release(graph, action, reviewer="发布人"):
    return server.app.test_client().post("/api/ont/release", json={"graph": graph, "action": action, "reviewer": reviewer})


def test_publishing_needs_a_passing_check_and_a_named_person(work):
    assert _release(_graph(work, "built_failed4", "fail"), "publish").status_code == 409
    ok = _graph(work, "built_ok1", "pass")
    assert _release(ok, "publish", reviewer="").status_code == 400
    r = _release(ok, "publish")
    assert r.status_code == 200 and r.get_json()["release"]["state"] == "published"
    got = server.app.test_client().get("/api/ont/release?graph=" + ok).get_json()
    assert got["state"] == "published" and got["by"] == "发布人" and got["usable"] is True
    assert _ask(ok).headers["X-DataMind-Graph-Release"] == "published"


def test_a_new_build_round_turns_a_published_graph_back_into_a_draft(work):
    key = _graph(work, "built_ok2", "pass", rounds=1)
    _release(key, "publish")
    _graph(work, key, "pass", rounds=2, extra=[{"name": "工序", "table": "dim_process", "kind": "object"}])
    assert server.app.test_client().get("/api/ont/release?graph=" + key).get_json()["state"] == "draft"


def test_a_human_edit_after_publishing_also_needs_republishing(work):
    key = _graph(work, "built_ok4", "pass")
    _release(key, "publish")
    c = server.app.test_client()
    r = c.post("/api/ont/apply", json={"graph": key, "reviewer": "审核人",
                                       "op": {"op": "set_alias", "target": "obj:客户", "params": {"aliases": ["买方"]}}})
    assert r.status_code == 200, r.get_data(as_text=True)      # 别名是不依赖编辑引擎的本地算子
    st = c.get("/api/ont/release?graph=" + key).get_json()
    assert st["state"] == "draft" and "重新发布" in st["reason"]


def test_publishing_keeps_an_immutable_copy_named_by_revision(work):
    key = _graph(work, "built_ok5", "pass")
    rel = _release(key, "publish").get_json()["release"]
    snap = work / "releases" / f"{key}@{rel['revision']}.json"
    assert rel["revision"].startswith("rev_") and snap.exists()
    before = snap.read_bytes()
    _graph(work, key, "pass", extra=[{"name": "工序", "table": "dim_process", "kind": "object"}])
    _release(key, "publish")
    assert snap.read_bytes() == before and len(list((work / "releases").glob(key + "@*.json"))) == 2


def test_withdrawing_and_strict_mode(work, monkeypatch):
    key = _graph(work, "built_ok3", "pass")
    draft = _graph(work, "built_review2", "review")
    _release(key, "publish")
    monkeypatch.setenv("DATAMIND_QA_REQUIRE_PUBLISHED", "1")
    assert _ask(key).status_code == 200 and _ask(draft).status_code == 409
    assert _release(key, "withdraw").get_json()["release"]["state"] == "blocked"
    assert _ask(key).status_code == 409
    monkeypatch.delenv("DATAMIND_QA_REQUIRE_PUBLISHED")
    assert _ask(key).status_code == 409                          # 撤回是明确的决定:默认模式下也不再用于正式问数
    assert _release(key, "publish").get_json()["release"]["state"] == "published" and _ask(key).status_code == 200


def test_built_list_shows_the_release_state(work):
    _graph(work, "built_failed5", "fail")
    rows = {x["key"]: x for x in server.app.test_client().get("/api/build/built").get_json()}
    assert rows["built_failed5"]["release"]["state"] == "blocked"
