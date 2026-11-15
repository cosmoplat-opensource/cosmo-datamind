# -*- coding: utf-8 -*-
"""同一对对象之间可以有多种业务关系(DR-114)。

工艺本体里这是常态:工单 → 工序 既有「首道工序」又有「末道工序」;工序 → 工序 既有正常流转又有返工。
此前关系以(源对象, 目标对象)为唯一标识,第二种角色在合并、评审、人审保留各处都被当成同一条。"""
import copy
import json

import pytest

import ir_shape
import relation_triage
import server
from tests.scenarios import bench

FIRST = {"source_concept": "工单", "target_concept": "工序", "role": "首道工序", "verb": "始于", "status": "verified",
         "evidence": {"child_key": "first_op_id", "parent_key": "op_id", "overlap": 100.0, "parent_unique": True, "name_ok": True}}
LAST = {"source_concept": "工单", "target_concept": "工序", "role": "末道工序", "verb": "止于", "status": "verified",
        "evidence": {"child_key": "last_op_id", "parent_key": "op_id", "overlap": 100.0, "parent_unique": True, "name_ok": True}}
OBJS = [{"name": "工单", "table": "work_order"}, {"name": "工序", "table": "operation"}]


def _ir(*rels):
    return {"objects": copy.deepcopy(OBJS), "relations": [copy.deepcopy(r) for r in rels]}


def _roles(ir):
    return sorted(r.get("role") or "" for r in ir["relations"])


# ── 身份 ────────────────────────────────────────────────────────────

def test_identity_includes_the_role_and_defaults_to_empty():
    assert ir_shape.rel_id(FIRST) == ("工单", "工序", "首道工序")
    assert ir_shape.rel_id({"source": "a", "target": "b"}) == ("a", "b", "")
    assert ir_shape.rel_ref(FIRST) == "rel:工单->工序#首道工序"
    assert ir_shape.rel_ref({"source_concept": "A", "target_concept": "B"}) == "rel:A->B"
    assert ir_shape.parse_rel_ref("rel:工单->工序#末道工序") == ("工单", "工序", "末道工序")
    assert ir_shape.parse_rel_ref("rel:A->B") == ("A", "B", None)
    assert ir_shape.parse_rel_ref("nonsense") is None


# ── 合并 ────────────────────────────────────────────────────────────

def test_merge_keeps_both_roles_between_the_same_pair():
    out, stat = server._merge_ir(_ir(FIRST), _ir(LAST))
    assert _roles(out) == ["末道工序", "首道工序"] and stat["relations_added"] == 1
    again, stat = server._merge_ir(out, _ir(LAST))                  # 同一角色再来一次不重复添加
    assert len(again["relations"]) == 2 and stat["relations_added"] == 0


def test_merge_without_roles_behaves_as_before():
    a = {k: v for k, v in FIRST.items() if k != "role"}
    b = {**{k: v for k, v in LAST.items() if k != "role"}, "status": "candidate"}
    out, stat = server._merge_ir(_ir(a), _ir(b))
    assert len(out["relations"]) == 1 and out["relations"][0]["evidence"]["child_key"] == "first_op_id"
    assert stat["relations_kept"] == 1


# ── 评审定位 ────────────────────────────────────────────────────────

def test_a_reference_with_a_role_finds_that_relation():
    ir = _ir(FIRST, LAST)
    assert server._find_rel_any(ir, "rel:工单->工序#末道工序")["verb"] == "止于"
    assert server._find_rel_any(ir, "rel:工单->工序#首道工序")["verb"] == "始于"
    assert server._find_rel_any(ir, "rel:工单->工序#不存在") is None


def test_a_reference_without_a_role_is_refused_when_it_is_ambiguous():
    with pytest.raises(ValueError) as e:
        server._find_rel_any(_ir(FIRST, LAST), "rel:工单->工序")
    assert "首道工序" in str(e.value) and "末道工序" in str(e.value)
    assert server._find_rel_any(_ir(FIRST), "rel:工单->工序")["verb"] == "始于"      # 只有一条时旧写法照常可用


def test_review_rows_carry_the_role_and_ops_address_one_role(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "WORK", str(tmp_path))
    cand = {**LAST, "status": "candidate"}
    (tmp_path / "built_roles1.json").write_text(json.dumps(_ir(FIRST, cand), ensure_ascii=False), encoding="utf-8")
    c = server.app.test_client()
    rows = c.get("/api/ont/review?graph=built_roles1").get_json()["rows"]
    assert sorted(r["role"] for r in rows) == ["末道工序", "首道工序"]
    assert sorted(r["ref"] for r in rows) == ["rel:工单->工序#末道工序", "rel:工单->工序#首道工序"]
    r = c.post("/api/ont/apply", json={"graph": "built_roles1", "reviewer": "审核人",
                                       "op": {"op": "confirm_relation", "target": "rel:工单->工序#末道工序", "reason": "业务确认"}})
    assert r.status_code == 200, r.get_data(as_text=True)
    after = {x["role"]: x for x in c.get("/api/ont/review?graph=built_roles1").get_json()["rows"]}
    assert after["末道工序"]["human_review"] == "approved" and not after["首道工序"]["human_review"]
    bad = c.post("/api/ont/apply", json={"graph": "built_roles1", "reviewer": "审核人",
                                         "op": {"op": "reject_relation", "target": "rel:工单->工序", "reason": "x"}})
    assert bad.status_code == 400 and "角色" in bad.get_json()["error"]


# ── 人审保留与分流 ──────────────────────────────────────────────────

def test_reviewed_relations_are_tracked_per_role(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "WORK", str(tmp_path))
    ir = _ir({**FIRST, "human_review": "approved"}, LAST)
    (tmp_path / "built_roles2.json").write_text(json.dumps(ir, ensure_ascii=False), encoding="utf-8")
    assert server._reviewed_relations("built_roles2") == {("工单", "工序", "首道工序"): "approved"}


def test_keep_reviewed_restores_only_the_reviewed_role():
    snap = {ir_shape.rel_id(FIRST): copy.deepcopy(FIRST)}
    ir = _ir(LAST)                                              # 首道工序那条被后续步骤移走了
    assert server._keep_reviewed(ir, snap) == {"restored": 1, "reverted": 0}
    assert _roles(ir) == ["末道工序", "首道工序"]


def test_triage_keep_set_is_per_role():
    weak = lambda role: {"source_concept": "工单", "target_concept": "工序", "role": role, "status": "candidate", "evidence": {}}
    ir = {"objects": copy.deepcopy(OBJS), "relations": [weak("首道工序"), weak("末道工序")]}
    relation_triage.triage(ir, keep={("工单", "工序", "首道工序")})
    assert _roles(ir) == ["首道工序"] and len(ir["rejected_relations"]) == 1


# ── 构建:模型提议里的两种角色都留下来 ───────────────────────────────

def test_a_build_keeps_two_roles_proposed_between_the_same_tables(tmp_path, make_sqlite):
    db = make_sqlite({
        "operation": ("operation_id INTEGER PRIMARY KEY, op_name TEXT", [(i, f"工序{i}") for i in range(1, 9)]),
        "work_order": ("wo_id INTEGER PRIMARY KEY, first_operation_id INTEGER, last_operation_id INTEGER",
                       [(i, 1 + i % 3, 5 + i % 4) for i in range(1, 41)]),
    })
    proposal = {"objects": [{"name": "工单", "table": "work_order", "kind": "event"}, {"name": "工序", "table": "operation", "kind": "object"}],
                "relations": [{"source": "工单", "target": "工序", "role": "首道工序", "verb": "始于", "child_key": "first_operation_id", "parent_key": "operation_id"},
                              {"source": "工单", "target": "工序", "role": "末道工序", "verb": "止于", "child_key": "last_operation_id", "parent_key": "operation_id"}]}
    work = tmp_path / "work"
    work.mkdir()
    with bench._patched(server, WORK=str(work), _BUILD_CONN_F=str(work / "build_connections.json"),
                        _build_intent=lambda *a, **k: {}, _build_summary=lambda *a, **k: "摘要",
                        _llm_semantic_review=lambda *a, **k: None):
        conn = bench._register(server, work, db, "工艺示例")
        _steps, done, ir, error = bench._build(server, conn, proposal)
    assert done and not error, error
    got = {r.get("role"): r for r in ir["relations"] if r.get("source_concept") == "工单" and r.get("target_concept") == "工序"}
    assert set(got) == {"首道工序", "末道工序"}
    assert got["首道工序"]["evidence"]["child_key"] == "first_operation_id" and got["末道工序"]["evidence"]["child_key"] == "last_operation_id"
    assert got["首道工序"]["status"] == got["末道工序"]["status"] == "verified"
