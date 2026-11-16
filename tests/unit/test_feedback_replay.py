# -*- coding: utf-8 -*-
"""反馈的回放验证(DR-137):反馈时保存答案的结果,之后按当前本体重新问一次,比对答案的数据有没有变。

- 「待改进」的反馈:回放后答案变了,可能是修改起了作用;没变则问题多半还在。是否改对仍需人确认。
- 「有用」的反馈:作为回归题。本体修改后批量回放,答案的数据变了的,提示可能退化。
比对的是返回的数据(行的集合、允许多带列),不是 SQL 文本:模型每次写法不同,数据相同就算一致。"""
import json

import pytest

import feedback_replay as F
import server

R1 = {"title": "各月毛利率", "sql": "SELECT m, r FROM t", "data": {"columns": ["m", "r"], "rows": [{"m": "2026-04", "r": 14.97},
                                                                                              {"m": "2026-05", "r": 14.71}]}}


def test_a_snapshot_keeps_a_digest_and_a_bounded_sample():
    (s,) = F.snapshot([{**R1, "data": {"columns": ["x"], "rows": [{"x": i} for i in range(500)]}}])
    assert s["row_count"] == 500 and len(s["rows"]) == F.SAMPLE_ROWS and s["digest"]
    assert F.snapshot([R1])[0]["digest"] == F.snapshot([{**R1, "sql": "SELECT m, r FROM t2"}])[0]["digest"]   # 只看数据


def test_the_same_data_written_differently_counts_as_the_same_answer():
    before = F.snapshot([R1])
    after = F.snapshot([{"title": "x", "sql": "SELECT month, amount, r FROM t2",
                         "data": {"columns": ["month", "amount", "r"],
                                  "rows": [{"month": "2026-05", "amount": 1, "r": 14.71}, {"month": "2026-04", "amount": 2, "r": 14.97}]}}])
    assert F.compare(before, after) == {"outcome": "same", "matched": 1, "of": 1}


def test_changed_and_missing_answers():
    before = F.snapshot([R1])
    changed = F.snapshot([{**R1, "data": {"columns": ["m", "r"], "rows": [{"m": "2026-04", "r": 34.1}]}}])
    assert F.compare(before, changed)["outcome"] == "changed"
    assert F.compare(before, [])["outcome"] == "no_answer"
    assert F.compare([], F.snapshot([R1]))["outcome"] == "no_baseline"


@pytest.mark.parametrize("verdict,outcome,needle", [("needs_work", "same", "没有变化"), ("needs_work", "changed", "请确认"),
                                                    ("useful", "same", "一致"), ("useful", "changed", "可能退化")])
def test_what_an_outcome_means_depends_on_the_kind_of_feedback(verdict, outcome, needle):
    assert needle in F.explain(verdict, outcome)


# ── 接口 ────────────────────────────────────────────────────────────────────

@pytest.fixture
def fb(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "QA_FB", str(tmp_path / "fb.json"))
    answers = {"n": 0, "results": [R1]}

    class Resp:
        def __init__(self, body):
            self._b = body
            self.status_code = 200

        def get_json(self):
            return self._b

    def fake_ask(question, graphs):
        answers["n"] += 1
        answers["last"] = (question, graphs)
        return {"results": answers["results"], "steps": [], "answer_record": {"answer_id": "a_x"}}
    monkeypatch.setattr(server, "_feedback_ask", fake_ask)
    return answers


def _post(path, body):
    return server.app.test_client().post(path, json=body)


def test_feedback_keeps_the_answer_and_a_replay_compares_against_it(fb):
    fid = _post("/api/chat/feedback", {"question": "各月毛利率", "verdict": "needs_work", "comment": "4 月不对",
                                       "graphs": ["demo"], "results": [R1]}).get_json()["id"]
    r = _post("/api/chat/feedback/replay", {"id": fid}).get_json()
    assert r["outcome"] == "same" and "没有变化" in r["explain"] and fb["last"] == ("各月毛利率", ["demo"])
    fb["results"] = [{**R1, "data": {"columns": ["m", "r"], "rows": [{"m": "2026-04", "r": 34.1}]}}]
    r = _post("/api/chat/feedback/replay", {"id": fid}).get_json()
    assert r["outcome"] == "changed"
    item = next(x for x in server.json.load(open(server.QA_FB)) if x["id"] == fid)
    assert [x["outcome"] for x in item["replays"]] == ["changed", "same"]


def test_resolving_records_whether_a_replay_backed_it(fb):
    fid = _post("/api/chat/feedback", {"question": "q", "verdict": "needs_work", "graphs": ["demo"], "results": [R1]}).get_json()["id"]
    fb["results"] = [{**R1, "data": {"columns": ["m"], "rows": [{"m": "x"}]}}]
    _post("/api/chat/feedback/replay", {"id": fid})
    assert _post("/api/chat/feedback/resolve", {"id": fid}).get_json()["ok"] is True
    item = next(x for x in server.json.load(open(server.QA_FB)) if x["id"] == fid)
    assert item["resolution"] == "replay_changed"
    fid2 = _post("/api/chat/feedback", {"question": "q2", "verdict": "needs_work"}).get_json()["id"]
    _post("/api/chat/feedback/resolve", {"id": fid2})
    item2 = next(x for x in server.json.load(open(server.QA_FB)) if x["id"] == fid2)
    assert item2["resolution"] == "manual"


def test_useful_feedback_is_replayed_as_a_regression_set(fb):
    for q in ("a", "b"):
        _post("/api/chat/feedback", {"question": q, "verdict": "useful", "graphs": ["demo"], "results": [R1]})
    _post("/api/chat/feedback", {"question": "no-snapshot", "verdict": "useful"})
    out = server._feedback_regression()
    assert out["replayed"] == 2 and out["same"] == 2 and out["skipped"] == 1 and out["changed"] == []
    fb["results"] = []
    out = server._feedback_regression()
    assert out["no_answer"] == 2


def test_bad_inputs_are_rejected(fb):
    assert _post("/api/chat/feedback/replay", {"id": "nope"}).status_code == 404
    assert _post("/api/chat/feedback", {"question": "q", "verdict": "useful", "results": "x"}).status_code == 400
    big = [{"title": "t", "sql": "s", "data": {"columns": ["a"], "rows": [{"a": 1}]}}] * 9
    assert _post("/api/chat/feedback", {"question": "q", "verdict": "useful", "results": big}).status_code == 400


# ── DR-138:多次回放,分开「本体修改带来的变化」与「模型每次写法不同带来的变化」 ─────────────────────────

def test_runs_that_disagree_are_reported_as_unstable(fb):
    fid = _post("/api/chat/feedback", {"question": "q", "verdict": "useful", "graphs": ["demo"], "results": [R1]}).get_json()["id"]
    seq = [[R1], [{**R1, "data": {"columns": ["m", "r"], "rows": [{"m": "x", "r": 1}]}}]]
    server_fake = server._feedback_ask

    def alternating(question, graphs):
        out = server_fake(question, graphs)
        out["results"] = seq[(fb["n"] - 1) % 2]
        return out
    import pytest as _p
    mp = _p.MonkeyPatch()
    mp.setattr(server, "_feedback_ask", alternating)
    try:
        r = _post("/api/chat/feedback/replay", {"id": fid, "runs": 2}).get_json()
    finally:
        mp.undo()
    assert r["outcome"] == "unstable" and r["runs"] == 2 and "随机" in r["explain"]


def test_a_changed_answer_is_checked_once_more_in_the_regression_set(fb):
    _post("/api/chat/feedback", {"question": "a", "verdict": "useful", "graphs": ["demo"], "results": [R1]})
    fb["results"] = [{**R1, "data": {"columns": ["m", "r"], "rows": [{"m": "x", "r": 1}]}}]
    out = server._feedback_regression()
    assert fb["n"] == 2 and len(out["changed"]) == 1               # 第一次变了,再问一次确认,仍然变了才列出
    assert "unstable" in out


def test_the_number_of_runs_is_bounded(fb):
    fid = _post("/api/chat/feedback", {"question": "q", "verdict": "useful", "results": [R1]}).get_json()["id"]
    assert _post("/api/chat/feedback/replay", {"id": fid, "runs": 9}).status_code == 400


def test_compare_runs_combines_outcomes():
    assert F.combine(["same", "same"]) == "same" and F.combine(["changed", "changed"]) == "changed"
    assert F.combine(["same", "changed"]) == "unstable" and F.combine(["no_answer", "same"]) == "unstable"
    assert F.combine(["no_baseline"]) == "no_baseline"


def test_stored_replays_and_the_list_carry_no_row_samples(fb):
    fid = _post("/api/chat/feedback", {"question": "q", "verdict": "needs_work", "graphs": ["demo"], "results": [R1]}).get_json()["id"]
    assert "results" in _post("/api/chat/feedback/replay", {"id": fid}).get_json()      # 本次回放的响应里仍有答案摘要
    stored = next(x for x in json.load(open(server.QA_FB, encoding="utf-8")) if x["id"] == fid)
    assert "results" not in stored["replays"][0]
    item = next(x for x in server.app.test_client().get("/api/chat/feedback").get_json()["items"] if x["id"] == fid)
    assert item["snapshot"] and all("rows" not in s and "sql" not in s for s in item["snapshot"])


def test_regression_jobs_are_kept_per_data_source(fb, monkeypatch):
    import threading
    gate = threading.Event()
    monkeypatch.setattr(server, "_feedback_regression", lambda: gate.wait(5) and {"replayed": 0})
    c = server.app.test_client()
    assert c.post("/api/chat/feedback/regression", json={}).status_code == 202
    assert c.post("/api/chat/feedback/regression", json={}).status_code == 409
    with monkeypatch.context() as m:                 # 换到另一个数据源:看不到、也不受前一个数据源的任务影响
        m.setattr(server, "_active_source", lambda: {"id": "other", "path": "/nonexistent/other.db"})
        other = c.get("/api/chat/feedback/regression").get_json()
    assert other["running"] is False and other["result"] is None
    gate.set()
