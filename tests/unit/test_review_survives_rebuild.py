# -*- coding: utf-8 -*-
"""人审结论在继续构建中保留(DR-097)。

人审写在编辑记录里,构建产物文件上看不到。继续构建时,分流、码表、交叉复核等步骤此前只看文件,
会把人工确认过的关系移出图谱或改写;之后回放这条确认时找不到关系,异常又被吞掉,审批无声消失。"""
import copy
import json
from pathlib import Path

import pytest

import relation_triage
import server
from tests.scenarios import bench, fixtures

PAIR = ("计量关系", "计量器具")          # 业务库夹具里唯一留作候选的关系(60 条孤儿记录,重叠率不足)


def _rel(ir, pair=PAIR):
    return next((r for r in ir.get("relations") or []
                 if (r.get("source_concept"), r.get("target_concept")) == pair), None)


# ── 分流 ──────────────────────────────────────────────────────────────

def _no_evidence_ir():
    return {"objects": [{"name": "A", "table": "a"}, {"name": "B", "table": "b"}],
            "relations": [{"source_concept": "A", "target_concept": "B", "status": "candidate", "evidence": {}}]}


def test_triage_moves_a_relation_without_key_evidence_out_of_the_graph():
    ir = _no_evidence_ir()
    relation_triage.triage(ir)
    assert ir["relations"] == [] and len(ir["rejected_relations"]) == 1


def test_triage_leaves_human_decided_relations_in_the_graph():
    ir = _no_evidence_ir()
    stat = relation_triage.triage(ir, keep={("A", "B")})
    assert len(ir["relations"]) == 1 and ir["rejected_relations"] == []
    assert "triage" not in ir["relations"][0] and stat["rejected"] == 0


def test_triage_does_not_accumulate_the_same_rejected_relation_across_rounds():
    ir = _no_evidence_ir()
    relation_triage.triage(ir)
    for _ in range(2):                       # 继续构建:模型再次提出同一条关系
        ir["relations"] = [{"source_concept": "A", "target_concept": "B", "status": "candidate", "evidence": {}}]
        relation_triage.triage(ir)
    assert len(ir["rejected_relations"]) == 1


# ── 收尾保底 ──────────────────────────────────────────────────────────

def test_keep_reviewed_restores_a_removed_relation_and_reverts_a_rewritten_one():
    kept = {"source_concept": "A", "target_concept": "B", "status": "candidate",
            "evidence": {"child_key": "b_id", "parent_key": "id", "overlap": 40}}
    rewritten = {"source_concept": "C", "target_concept": "D", "status": "candidate",
                 "evidence": {"child_key": "d_code", "parent_key": "code"}}
    snapshot = {("A", "B"): copy.deepcopy(kept), ("C", "D"): copy.deepcopy(rewritten)}
    ir = {"relations": [{"source_concept": "C", "target_concept": "D", "status": "verified", "verb": "取值于",
                         "evidence": {"child_key": "kind", "parent_key": "dict_value", "source": "codebook"}}],
          "rejected_relations": [dict(kept, triage={"decision": "reject", "reason": "x"})],
          "deferred_relations": []}
    stat = server._keep_reviewed(ir, snapshot)
    assert stat == {"restored": 1, "reverted": 1}
    assert _rel(ir, ("A", "B")) == kept and ir["rejected_relations"] == []
    assert _rel(ir, ("C", "D")) == rewritten


def test_keep_reviewed_accepts_stronger_data_evidence_on_the_same_key():
    before = {"source_concept": "A", "target_concept": "B", "status": "candidate",
              "evidence": {"child_key": "b_id", "parent_key": "id", "overlap": 40}}
    after = {"source_concept": "A", "target_concept": "B", "status": "verified",
             "evidence": {"child_key": "b_id", "parent_key": "id", "overlap": 100}}
    ir = {"relations": [copy.deepcopy(after)]}
    assert server._keep_reviewed(ir, {("A", "B"): before}) == {"restored": 0, "reverted": 0}
    assert ir["relations"] == [after]


# ── 回放失败留痕 ──────────────────────────────────────────────────────

def _graph_with_edits(tmp_path, monkeypatch, ops):
    monkeypatch.setattr(server, "WORK", str(tmp_path))
    ir = {"objects": [{"name": "A", "table": "a"}, {"name": "B", "table": "b"}],
          "relations": [{"source_concept": "A", "target_concept": "B", "status": "candidate"}]}
    (tmp_path / "built_t1.json").write_text(json.dumps(ir, ensure_ascii=False), encoding="utf-8")
    Path(server._edits_path("built_t1")).write_text(json.dumps({"version": 1, "ops": ops}, ensure_ascii=False),
                                                    encoding="utf-8")
    return "built_t1"


def test_failed_replay_is_reported_instead_of_vanishing(tmp_path, monkeypatch):
    key = _graph_with_edits(tmp_path, monkeypatch, [
        {"op": "confirm_relation", "target": "rel:A->B", "reviewer": "张三"},
        {"op": "confirm_relation", "target": "rel:A->GONE", "reviewer": "张三"}])
    ir = server.load_ir_edited(key)
    assert _rel(ir, ("A", "B"))["human_review"] == "approved"
    failed = server.edit_replay_failures(key)
    assert [(f["index"], f["op"], f["target"]) for f in failed] == [(1, "confirm_relation", "rel:A->GONE")]
    assert "不存在" in failed[0]["error"]
    body = server.app.test_client().get("/api/ont/review?graph=" + key).get_json()
    assert body["replay_failed"] == failed


def test_clean_replay_reports_nothing(tmp_path, monkeypatch):
    key = _graph_with_edits(tmp_path, monkeypatch, [{"op": "confirm_relation", "target": "rel:A->B"}])
    server.load_ir_edited(key)
    assert server.edit_replay_failures(key) == []


# ── 端到端:确认 → 继续构建 → 结论仍在 ─────────────────────────────────

@pytest.fixture
def business_graph(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    fx = fixtures.business(str(tmp_path / "business.db"))
    with bench._patched(server, WORK=str(work), _BUILD_CONN_F=str(work / "build_connections.json"),
                        _build_intent=lambda *a, **k: {}, _build_summary=lambda *a, **k: "摘要",
                        _llm_semantic_review=lambda *a, **k: None):
        conn = bench._register(server, work, fx["db"], "业务回归库")
        _steps, done, ir, error = bench._build(server, conn, fx["proposal"])
        assert done and not error and _rel(ir)["status"] == "candidate"
        yield conn, fx, done["graph_key"]


def _apply(graph, op):
    r = server.app.test_client().post("/api/ont/apply", json={
        "graph": graph, "reviewer": "审核人", "op": dict(op, reason="业务确认")}).get_json()
    assert r.get("ok"), r


def test_confirmed_relation_survives_a_round_whose_triage_would_reject_it(business_graph, monkeypatch):
    conn, fx, graph = business_graph
    _apply(graph, {"op": "confirm_relation", "target": "rel:%s->%s" % PAIR})
    real = relation_triage.classify

    def classify(rel, *a, **k):              # 本轮证据变了(例如复核记了冲突):这条关系按规则应被驳回
        if (rel.get("source_concept"), rel.get("target_concept")) == PAIR:
            return "reject", "交叉复核强冲突", 0.0
        return real(rel, *a, **k)
    monkeypatch.setattr(relation_triage, "classify", classify)
    steps, done, revised, error = bench._build(server, conn, fx["proposal"], base_graph=graph)
    assert done and not error
    assert _rel(revised) is not None, "人工确认过的关系被移出了图谱"
    reviewed = _rel(server.load_ir_edited(graph))
    assert reviewed["human_review"] == "approved" and reviewed["status"] == "asserted"
    assert server.edit_replay_failures(graph) == []


def test_rejected_relation_is_not_rewritten_as_verified_by_a_later_step(business_graph, monkeypatch):
    conn, fx, graph = business_graph
    _apply(graph, {"op": "reject_relation", "target": "rel:%s->%s" % PAIR})
    before = copy.deepcopy(_rel(server.load_ir(graph)))

    def rewrite(ir):                         # 后续步骤(码表、复核)把它改写成按另一组键验证
        r = _rel(ir)
        r.update(status="verified", evidence={"child_key": "energy_type", "parent_key": "dev_no", "source": "codebook"})
        return 0
    monkeypatch.setattr(server, "_dedupe_parents", rewrite)
    _steps, done, revised, error = bench._build(server, conn, fx["proposal"], base_graph=graph)
    assert done and not error
    assert _rel(revised)["evidence"].get("child_key") == before["evidence"].get("child_key")
    assert _rel(revised)["status"] == before["status"]
    assert _rel(server.load_ir_edited(graph))["status"] == "rejected"
