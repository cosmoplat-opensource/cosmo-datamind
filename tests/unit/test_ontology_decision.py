# -*- coding: utf-8 -*-
"""工艺路线层与决策层(DR-117):约束、路线、点亮矩阵挂在已建模的对象上,路线由关系按方向核验。"""
import json
from pathlib import Path

import pytest

import build_quality
import ontology_decision as od
import server

IR = {"scenario": {"name": "工艺示例"},
      "objects": [{"name": "工单", "table": "wo", "kind": "event"}, {"name": "绕线", "table": "op_wind", "kind": "object"},
                  {"name": "干燥", "table": "op_dry", "kind": "object"}, {"name": "总装", "table": "op_asm", "kind": "object"}],
      "relations": [
          {"source_concept": "工单", "target_concept": "绕线", "role": "首道工序", "verb": "始于", "status": "verified",
           "evidence": {"child_key": "first_op", "parent_key": "op_id", "overlap": 100.0, "parent_unique": True, "name_ok": True}},
          {"source_concept": "绕线", "target_concept": "干燥", "role": "流转", "verb": "流转至", "status": "asserted"},
          {"source_concept": "干燥", "target_concept": "总装", "role": "流转", "verb": "流转至", "status": "asserted"}]}
DRY = {"id": "dry_wait_72h", "cn": "干燥等待时限", "on": "干燥", "kind": "batching", "rule": "同批凑炉的等待不超过 72 小时",
       "params": {"max_wait_h": 72}, "unit": "小时", "purpose": "防止绝缘受潮", "exceptions": "返工件单独成批"}


# ── 约束 ────────────────────────────────────────────────────────────

def test_a_constraint_is_hard_by_default_and_keeps_its_business_meaning():
    c = od.normalize_constraint(DRY, IR)
    assert c["hardness"] == "hard" and c["status"] == "candidate"
    assert c["unit"] == "小时" and c["purpose"] == "防止绝缘受潮" and c["exceptions"] == "返工件单独成批"


def test_replayable_evidence_makes_a_constraint_evidenced_and_only_a_named_person_certifies():
    code = od.normalize_constraint({**DRY, "evidence": [{"type": "code", "source": "scheduler.py", "lines": "169-180"}]}, IR)
    assert code["status"] == "evidenced"
    talk = od.normalize_constraint({**DRY, "evidence": [{"type": "interview", "source": "计划员访谈"}]}, IR)
    assert talk["status"] == "candidate"
    with pytest.raises(ValueError, match="确认人"):
        od.normalize_constraint({**DRY, "status": "certified"}, IR)
    assert od.normalize_constraint({**DRY, "status": "certified", "certified_by": "工艺负责人"}, IR)["status"] == "certified"


@pytest.mark.parametrize("bad,word", [
    ({"on": "喷漆"}, "不在本体中"), ({"kind": "magic"}, "kind"), ({"rule": ""}, "rule"),
    ({"id": "1bad"}, "id"), ({"hardness": "maybe"}, "hardness"), ({"evidence": [{"type": "code"}]}, "source"),
])
def test_malformed_constraints_are_refused(bad, word):
    with pytest.raises(ValueError, match=word):
        od.normalize_constraint({**DRY, **bad}, IR)


# ── 工艺路线 ────────────────────────────────────────────────────────

def test_a_route_must_use_modelled_objects_in_order():
    r = od.normalize_route({"id": "main", "cn": "主工艺路线", "steps": ["工单", "绕线", "干燥", "总装"], "roles": ["首道工序", "流转", "流转"]}, IR)
    assert r["steps"] == ["工单", "绕线", "干燥", "总装"] and r["status"] == "candidate"
    with pytest.raises(ValueError, match="不在本体中"):
        od.normalize_route({"id": "x", "cn": "x", "steps": ["工单", "喷漆"]}, IR)
    with pytest.raises(ValueError, match="steps"):
        od.normalize_route({"id": "x", "cn": "x", "steps": ["工单"]}, IR)
    with pytest.raises(ValueError, match="roles"):
        od.normalize_route({"id": "x", "cn": "x", "steps": ["工单", "绕线"], "roles": ["a", "b"]}, IR)


def test_declared_routes_become_directed_acceptance_questions():
    ir = {**IR, "routes": [od.normalize_route({"id": "main", "cn": "主工艺路线", "steps": ["工单", "绕线", "干燥", "总装"]}, IR),
                           {"id": "old", "cn": "旧路线", "steps": ["总装", "干燥"], "status": "deprecated"}]}
    cqs = od.route_cqs(ir)
    assert [c["route_id"] for c in cqs] == ["main"] and cqs[0]["route"] == ["工单", "绕线", "干燥", "总装"]


def test_quality_check_verifies_declared_routes_by_direction():
    good = {**IR, "routes": [od.normalize_route({"id": "main", "cn": "主工艺路线", "steps": ["工单", "绕线", "干燥", "总装"]}, IR)]}
    rep = build_quality.evaluate(good)
    assert rep["cq"]["provided"] and rep["cq"]["items"][0]["verdict"] == "answerable"
    back = {**IR, "routes": [od.normalize_route({"id": "rev", "cn": "倒着的路线", "steps": ["总装", "干燥", "绕线"]}, IR)]}
    rep = build_quality.evaluate(back)
    assert rep["cq"]["items"][0]["verdict"] == "unanswerable" and rep["result"] != "pass"
    assert any(g["type"] == "cq_unanswerable" for g in rep["gaps"])


def test_quality_check_lists_decision_items_still_waiting_for_a_person():
    ir = {**IR, "constraints": [od.normalize_constraint(DRY, IR)],
          "routes": [od.normalize_route({"id": "main", "cn": "主工艺路线", "steps": ["工单", "绕线"]}, IR)]}
    types = {g["type"] for g in build_quality.evaluate(ir)["review_queue"]}
    assert {"constraints_without_evidence", "routes_without_evidence"} <= types


# ── 点亮矩阵与审计 ──────────────────────────────────────────────────

def test_audit_counts_evidence_rules_and_data_sources():
    ir = {**IR, "constraints": [od.normalize_constraint(DRY, IR),
                                od.normalize_constraint({**DRY, "id": "cap", "hardness": "soft", "evidence": [{"type": "data", "source": "排程记录"}]}, IR)],
          "lighting": {"objects": {"工单": {"status": "lit"}, "干燥": {"status": "derived"}}, "unlit": [{"attribute": "炉温曲线"}]}}
    a = od.audit(ir, rules=[{"note": "cap"}])
    assert a["constraints"] == 2 and a["constraints_evidenced"] == 1 and a["constraints_hard"] == 1
    assert a["constraints_without_rule"] == ["dry_wait_72h"]
    assert a["lighting"]["lit"] == 1 and a["lighting"]["derived"] == 1 and set(a["objects_without_lighting"]) == {"绕线", "总装"}
    assert a["unlit_attributes"] == 1
    with pytest.raises(ValueError):
        od.normalize_lighting({"status": "bright"})


# ── 接口 ────────────────────────────────────────────────────────────

@pytest.fixture
def graph(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "WORK", str(tmp_path))
    (tmp_path / "built_dec1.json").write_text(json.dumps(IR, ensure_ascii=False), encoding="utf-8")
    return "built_dec1", server.app.test_client()


def test_constraints_can_be_saved_listed_and_deleted(graph):
    key, c = graph
    r = c.post(f"/api/ont/constraints/{key}", json={"constraint": DRY})
    assert r.status_code == 200 and r.get_json()["constraint"]["status"] == "candidate"
    assert c.post(f"/api/ont/constraints/{key}", json={"constraint": {**DRY, "on": "喷漆"}}).status_code == 400
    got = c.get(f"/api/ont/constraints/{key}").get_json()
    assert [x["id"] for x in got["constraints"]] == ["dry_wait_72h"] and got["audit"]["constraints"] == 1
    assert c.post(f"/api/ont/constraints/{key}/delete", json={"id": "dry_wait_72h"}).get_json()["total"] == 0
    assert c.post(f"/api/ont/constraints/{key}/delete", json={"id": "nope"}).status_code == 400


def test_saving_a_route_reports_whether_the_relations_support_it(graph):
    key, c = graph
    ok = c.post(f"/api/ont/routes/{key}", json={"route": {"id": "main", "cn": "主工艺路线", "steps": ["工单", "绕线", "干燥", "总装"]}}).get_json()
    assert ok["check"]["verdict"] == "intact"
    rev = c.post(f"/api/ont/routes/{key}", json={"route": {"id": "rev", "cn": "倒着的", "steps": ["总装", "干燥"]}}).get_json()
    assert rev["check"]["verdict"] == "reversed"                      # 路线照存:它是声明,成不成立由验收检查持续核验
    listed = c.get(f"/api/ont/routes/{key}").get_json()
    assert {r["id"]: r["check"]["verdict"] for r in listed["routes"]} == {"main": "intact", "rev": "reversed"}
    assert c.post(f"/api/ont/routes/{key}/delete", json={"id": "rev"}).get_json()["total"] == 1


def test_lighting_and_audit_endpoints(graph):
    key, c = graph
    r = c.post(f"/api/ont/lighting/{key}", json={"objects": {"工单": {"status": "lit", "source": "MES 工单表"}},
                                                  "unlit": [{"attribute": "炉温曲线", "needs": "设备采集", "why": "干燥质量"}]})
    assert r.status_code == 200 and r.get_json()["objects"] == 1
    assert c.post(f"/api/ont/lighting/{key}", json={"objects": {"喷漆": {"status": "lit"}}}).status_code == 400
    a = c.get(f"/api/ont/decision_audit/{key}").get_json()
    assert a["lighting"]["lit"] == 1 and a["unlit_attributes"] == 1


def test_builtin_graphs_and_bad_keys_are_handled(graph):
    _key, c = graph
    assert c.get("/api/ont/constraints/a..b").status_code == 400
    assert c.get("/api/ont/routes/built_missing").status_code == 404


def test_the_check_returned_on_save_counts_human_confirmed_relations(graph):
    """保存路线时返回的核验结果要和之后查看时一致:人工确认过的关系算强关系。部署测试中发现保存时用的是未含人工修改的图谱。"""
    key, c = graph
    base = json.loads(json.dumps(IR, ensure_ascii=False))
    for r in base["relations"]:
        r["status"] = "candidate"
    (Path(server.WORK) / f"{key}.json").write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
    route = {"route": {"id": "two", "cn": "两步", "steps": ["工单", "绕线"]}}
    assert c.post(f"/api/ont/routes/{key}", json=route).get_json()["check"]["verdict"] == "weak"
    rel = next(r for r in base["relations"] if {r["source_concept"], r["target_concept"]} == {"工单", "绕线"})
    ok = c.post("/api/ont/apply", json={"graph": key, "reviewer": "验收员",
                                        "op": {"op": "confirm_relation", "target": f"rel:{rel['source_concept']}->{rel['target_concept']}"}})
    assert ok.status_code == 200, ok.get_json()
    saved = c.post(f"/api/ont/routes/{key}", json=route).get_json()["check"]["verdict"]
    listed = c.get(f"/api/ont/routes/{key}").get_json()["routes"][0]["check"]["verdict"]
    assert saved == listed == "intact"
