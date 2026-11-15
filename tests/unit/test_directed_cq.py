# -*- coding: utf-8 -*-
"""按方向核验的工艺类验收问题(DR-116)。

结构类验收问题用无向连通判断「两个对象连不连得上」,正着问、反着问都判可回答。
工艺路线要回答的是「能不能按工序顺序从这一步走到下一步」,方向反了、角色错了都不算通。"""
import cq_check
import server

IR = {"objects": [{"name": "工单", "table": "wo"}, {"name": "绕线", "table": "op_wind"}, {"name": "干燥", "table": "op_dry"},
                  {"name": "总装", "table": "op_asm"}, {"name": "检验", "table": "op_qc"}],
      "relations": [
          {"source_concept": "工单", "target_concept": "绕线", "role": "首道工序", "verb": "始于", "status": "verified"},
          {"source_concept": "绕线", "target_concept": "干燥", "role": "流转", "verb": "流转至", "status": "verified"},
          {"source_concept": "干燥", "target_concept": "总装", "role": "流转", "verb": "流转至", "status": "asserted"},
          {"source_concept": "总装", "target_concept": "检验", "role": "流转", "verb": "流转至", "status": "candidate"},
          {"source_concept": "检验", "target_concept": "总装", "role": "返工", "verb": "返工至", "status": "verified"},
      ]}


def test_a_route_in_process_order_is_intact():
    r = cq_check.check_route(["工单", "绕线", "干燥", "总装"], IR)
    assert r["verdict"] == "intact" and [s["status"] for s in r["segments"]] == ["ok", "ok", "ok"]
    assert r["segments"][0]["role"] == "首道工序"


def test_the_same_route_backwards_is_reported_as_reversed_not_answerable():
    r = cq_check.check_route(["总装", "干燥", "绕线"], IR)
    assert r["verdict"] == "reversed" and all(s["status"] == "reversed" for s in r["segments"])
    undirected = cq_check.check_chain(["总装", "干燥", "绕线"], IR)
    assert undirected["verdict"] == "intact"                       # 无向判定照旧:它回答的是连通,不是顺序


def test_a_candidate_step_is_weak_and_a_missing_step_is_broken():
    assert cq_check.check_route(["干燥", "总装", "检验"], IR)["verdict"] == "weak"
    r = cq_check.check_route(["绕线", "总装"], IR)                  # 没有直接的流转关系,不借道
    assert r["verdict"] == "broken" and r["segments"][0]["status"] == "broken"


def test_a_step_can_require_a_role():
    assert cq_check.check_route(["检验", "总装"], IR, roles=["返工"])["verdict"] == "intact"
    r = cq_check.check_route(["检验", "总装"], IR, roles=["流转"])
    assert r["verdict"] == "reversed" and "流转" in r["segments"][0]["note"]
    assert cq_check.check_route(["总装", "检验"], IR, roles=["返工"])["verdict"] == "reversed"


def test_unknown_or_too_short_routes_are_rejected():
    assert cq_check.check_route(["工单"], IR)["verdict"] == "invalid"
    assert cq_check.check_route(["工单", "喷漆"], IR)["verdict"] == "unanswerable"


def test_acceptance_questions_with_a_route_are_checked_by_direction():
    rep = cq_check.check_all([
        {"q": "工单从绕线到总装的工序顺序", "route": ["工单", "绕线", "干燥", "总装"]},
        {"q": "倒着走", "route": ["总装", "干燥"]},
        {"q": "总装和干燥有没有关系", "expect": ["总装", "干燥"]},
    ], IR)
    verdicts = [i["verdict"] for i in rep["items"]]
    assert verdicts == ["answerable", "unanswerable", "answerable"]
    assert rep["items"][0]["kind"] == "directed" and rep["items"][1]["kind"] == "directed"
    assert "方向" in rep["items"][1]["reason"] and rep["items"][2].get("kind", "structural") == "structural"


def test_build_requests_keep_the_route_of_an_acceptance_question():
    got = server._normalize_build_cqs([{"q": "顺序", "route": ["工单", "绕线"], "roles": ["首道工序"]}, {"q": "x", "route": "bad"}])
    assert got[0] == {"q": "顺序", "expect": [], "route": ["工单", "绕线"], "roles": ["首道工序"]}
    assert "route" not in got[1]


def test_chain_endpoint_can_check_direction(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(server, "WORK", str(tmp_path))
    (tmp_path / "built_route1.json").write_text(json.dumps(IR, ensure_ascii=False), encoding="utf-8")
    c = server.app.test_client()
    fwd = c.post("/api/ont/chain", json={"graph": "built_route1", "chain": ["绕线", "干燥"], "directed": True}).get_json()
    back = c.post("/api/ont/chain", json={"graph": "built_route1", "chain": ["干燥", "绕线"], "directed": True}).get_json()
    assert fwd["verdict"] == "intact" and back["verdict"] == "reversed"
