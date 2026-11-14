# -*- coding: utf-8 -*-
"""评审台(DR-107):评审者要能看到每条关系为什么被提出、证据是什么;分流移出的关系要看得见;
多条关系可以一次确认或否决,且要么全部记录、要么一条都不记。"""
import json

import pytest

import server
from tests.scenarios import bench, fixtures

PAIR = ("计量关系", "计量器具")          # 候选:80 个引用里 60 个指向不存在的设备


@pytest.fixture
def graph(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    fx = fixtures.business(str(tmp_path / "business.db"))
    proposal = json.loads(json.dumps(fx["proposal"]))
    proposal["relations"].append({"source": "企业", "target": "能源类型", "verb": "消耗"})     # 没有任何键证据:会被分流移出
    with bench._patched(server, WORK=str(work), _BUILD_CONN_F=str(work / "build_connections.json"),
                        _build_intent=lambda *a, **k: {}, _build_summary=lambda *a, **k: "摘要",
                        _llm_semantic_review=lambda *a, **k: None):
        conn = bench._register(server, work, fx["db"], "业务回归库")
        _steps, done, _ir, error = bench._build(server, conn, proposal)
        assert done and not error
        yield done["graph_key"]


def _review(key):
    return server.app.test_client().get("/api/ont/review?graph=" + key).get_json()


def _row(d, pair=PAIR):
    return next(r for r in d["rows"] if (r["s"], r["t"]) == pair)


def test_review_rows_explain_the_evidence_behind_each_relation(graph):
    row = _row(_review(graph))
    ev = row["evidence"]
    assert ev["child_key"] == "rel_obj_no" and ev["parent_key"] == "dev_no"
    assert ev["child_distinct"] == 80 and ev["matched_distinct"] == 20
    assert ev["parent_unique"] is True and ev["orphan_rate"] == pytest.approx(0.75)
    assert row["pending"] is True and "25%" in row["note"]              # 裁决给出的说明
    assert "重叠率" in row["why_review"]                      # 分流给出的「还缺什么」
    assert row["child_table"] == "measure_relation" and row["parent_table"] == "inst_device"


def test_relations_set_aside_by_triage_are_listed_with_the_reason(graph):
    d = _review(graph)
    aside = [x for x in d["set_aside"] if (x["s"], x["t"]) == ("企业", "能源类型")]
    assert aside and aside[0]["decision"] in ("reject", "defer") and aside[0]["reason"]
    assert d["counts"]["set_aside"] == len(d["set_aside"]) >= 1


def test_sample_values_show_what_matches_and_what_does_not(graph):
    r = server.app.test_client().get("/api/ont/relation/%s?s=%s&t=%s&samples=1" % (graph, *PAIR))
    s = r.get_json()["samples"]
    assert s["child"] == "measure_relation.rel_obj_no" and s["parent"] == "inst_device.dev_no"
    assert 1 <= len(s["matched"]) <= 5 and all(v.startswith("D") for v in s["matched"])
    assert 1 <= len(s["unmatched"]) <= 5 and all(v.startswith("GONE") for v in s["unmatched"])


def test_samples_are_only_computed_when_asked(graph):
    d = server.app.test_client().get("/api/ont/relation/%s?s=%s&t=%s" % (graph, *PAIR)).get_json()
    assert "samples" not in d


def _batch(key, ops, reviewer="审核人"):
    return server.app.test_client().post("/api/ont/apply", json={
        "graph": key, "reviewer": reviewer, "source": "review", "ops": ops})


def test_batch_review_records_every_decision_with_reviewer_and_source(graph):
    ops = [{"op": "confirm_relation", "target": "rel:%s->%s" % PAIR, "reason": "业务确认"},
           {"op": "reject_relation", "target": "rel:计量器具->企业", "reason": "口径不同"}]
    r = _batch(graph, ops)
    assert r.status_code == 200 and r.get_json()["applied"] == 2
    d = _review(graph)
    assert _row(d)["human_review"] == "approved" and _row(d, ("计量器具", "企业"))["human_review"] == "rejected"
    log = server._load_edits(graph)["ops"]
    assert [o["source"] for o in log] == ["review", "review"] and {o["reviewer"] for o in log} == {"审核人"}


def test_batch_review_is_all_or_nothing(graph):
    ops = [{"op": "confirm_relation", "target": "rel:%s->%s" % PAIR},
           {"op": "confirm_relation", "target": "rel:不存在->企业"}]
    r = _batch(graph, ops)
    assert r.status_code == 400 and r.get_json()["failed_index"] == 1
    assert server._load_edits(graph)["ops"] == [] and _row(_review(graph))["human_review"] == ""


def test_batch_review_needs_a_reviewer_and_a_bounded_list(graph):
    op = {"op": "confirm_relation", "target": "rel:%s->%s" % PAIR}
    assert _batch(graph, [op], reviewer="").status_code == 400
    assert _batch(graph, []).status_code == 400
    assert _batch(graph, [op] * 501).status_code == 400
    assert _batch(graph, [{"op": "rename_everything", "target": "x"}]).status_code == 400


def test_samples_work_for_graphs_whose_objects_list_tables_instead_of_one_table(make_sqlite, monkeypatch):
    db = make_sqlite({"p": ("id TEXT", [("a",), ("b",)]), "c": ("pid TEXT", [("a",), ("zz",)])})
    monkeypatch.setattr(server, "_source_graph_path", lambda ir: db)
    ir = {"objects": [{"name": "c", "tables": ["c"]}, {"name": "p", "tables": ["p"]}], "relations": []}
    got = server._relation_samples(ir, "c", "p", {"child_key": "pid", "parent_key": "id"})
    assert got["matched"] == ["a"] and got["unmatched"] == ["zz"]
