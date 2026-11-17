# -*- coding: utf-8 -*-
"""DR-063 接线:构建后自动复核,问数时套用清洗规则。

复核与清洗的方法在技能里(DR-062);这里锁住两个接入点的行为:
构建选中技能时自动运行并改写产物,问数时用只读连接上的临时视图套用异常编码值更正,
源库不变,清洗 SQL 只由结构化字段重建、标识符逐一核对、取值按字面量转义。
"""
import json
import os
import sqlite3

import pytest

import build_cleaning

server = pytest.importorskip("server")

from test_cross_check_skill import SKILL, _fixture, _ir  # noqa: E402


def _built(make_sqlite):
    db = _fixture(make_sqlite)
    ir = _ir()
    summary = build_cleaning.run_after_build(ir, db, SKILL)
    return db, ir, summary


def test_run_after_build_rewrites_ir_and_records_summary(make_sqlite):
    _db, ir, summary = _built(make_sqlite)
    assert summary["verdicts"]["refuted"] == 3 and summary["cleaning"]["outlier_code"] >= 1
    assert ir["scenario"]["cross_check"]["verified"] == 5
    bad = ir["relations"][1]
    assert bad["status"] == "candidate" and bad["evidence"]["cross_check"]["prev_status"] == "verified"
    measure = next(o for o in ir["objects"] if o["name"] == "measure")
    kinds = {r["kind"] for r in measure["evidence"]["cleaning"]}
    assert {"codebook", "outlier_code", "orphan_reference"} <= kinds
    assert "交叉复核" in build_cleaning.build_step_text(summary)
    assert "异常编码值" in build_cleaning.clean_step_text(summary)


def test_rerun_does_not_accumulate_stale_rules(make_sqlite):
    db, ir, _ = _built(make_sqlite)
    n = len(next(o for o in ir["objects"] if o["name"] == "measure")["evidence"]["cleaning"])
    build_cleaning.run_after_build(ir, db, SKILL)
    assert len(next(o for o in ir["objects"] if o["name"] == "measure")["evidence"]["cleaning"]) == n


def test_query_sees_corrected_codes_through_temp_view_and_source_is_untouched(make_sqlite):
    db, ir, _ = _built(make_sqlite)
    rules = build_cleaning.rules_by_table(ir)
    sql = "SELECT energy_type, COUNT(*) AS n FROM measure WHERE rel_obj_no LIKE 'D%' GROUP BY 1 ORDER BY 1"
    raw = server.q(sql, db=db)
    cleaned = server.q(sql, db=db, clean=rules)
    assert {r["energy_type"]: r["n"] for r in raw["rows"]} == {"2": 37, "3": 3}
    assert {r["energy_type"]: r["n"] for r in cleaned["rows"]} == {"2": 40}
    assert cleaned["cleaning"][0]["table"] == "measure" and cleaned["cleaning"][0]["column"] == "energy_type"
    assert any("孤儿记录" in n for n in cleaned["cleaning_notes"])
    with sqlite3.connect(db) as con:     # 源库不变
        assert con.execute("SELECT COUNT(*) FROM measure WHERE energy_type='3'").fetchone()[0] == 3
    # 条件外的行(指向系统的一侧)不被更正
    other = server.q("SELECT COUNT(*) AS n FROM measure WHERE rel_obj_type=1 AND energy_type='2'", db=db, clean=rules)
    assert other["rows"][0]["n"] == 10


def test_views_are_rebuilt_from_structured_fields_only(make_sqlite):
    db, ir, _ = _built(make_sqlite)
    rules = build_cleaning.rules_by_table(ir)
    rule = next(r for r in rules["measure"] if r["kind"] == "outlier_code")
    rule["sql"] = "1); DROP TABLE measure; --"                 # 存档的 sql 字段从不执行
    rule["groups"][0]["expected"] = "2' OR '1'='1"             # 取值按字面量转义
    out = server.q("SELECT COUNT(*) AS n FROM measure", db=db, clean=rules)
    assert out["rows"][0]["n"] > 0
    bogus = {"measure": [{**rule, "column": "no_such_col"}]}
    assert "cleaning" not in server.q("SELECT COUNT(*) AS n FROM measure", db=db, clean=bogus)


def test_malformed_rule_structure_is_skipped_not_raised(make_sqlite):
    """规则来自可编辑的 IR:via / condition 不是对象时放弃这条规则,问数照常执行。"""
    db, ir, _ = _built(make_sqlite)
    rules = build_cleaning.rules_by_table(ir)
    rule = next(r for r in rules["measure"] if r["kind"] == "outlier_code")
    for bad in ({"via": "x"}, {"condition": "x"}):
        broken = {"measure": [{**rule, "category": {**rule["category"], **bad}}]}
        out = server.q("SELECT COUNT(*) AS n FROM measure", db=db, clean=broken)
        assert out["rows"][0]["n"] > 0 and "cleaning" not in out


def test_upload_tables_are_redirected_to_the_cleaning_view(make_sqlite, tmp_path):
    db, ir, _ = _built(make_sqlite)
    main = tmp_path / "main.db"
    sqlite3.connect(main).close()
    con = sqlite3.connect(f"file:{main}?mode=ro", uri=True)
    con.execute(f"ATTACH DATABASE 'file:{db}?mode=ro' AS up")
    sql, applied = build_cleaning.apply_views(con, build_cleaning.rules_by_table(ir),
                                              "SELECT energy_type, COUNT(*) n FROM up.measure WHERE rel_obj_no LIKE 'D%' GROUP BY 1")
    assert applied and "up.measure" not in sql
    assert dict(con.execute(sql).fetchall()) == {"2": 40}


def test_context_text_gives_the_model_codebooks_and_corrections(make_sqlite):
    _db, ir, _ = _built(make_sqlite)
    text = build_cleaning.context_text(build_cleaning.rules_by_table(ir), ["measure", "up.price"])
    assert "measure.energy_type 取值含义" in text and "2=电" in text
    assert "自动更正" in text and "孤儿记录" in text
    assert build_cleaning.context_text(build_cleaning.rules_by_table(ir), ["unrelated"]) == ""


def test_chat_applies_cleaning_end_to_end(make_sqlite, monkeypatch):
    db, ir, _ = _built(make_sqlite)
    monkeypatch.setattr(server, "DB", db)
    monkeypatch.setattr(server, "UPLOAD_DB", db + ".missing")
    monkeypatch.setattr(server, "_anchor_ir", lambda keys: (json.loads(json.dumps(ir)), ["built_x"], ""))
    sql = "SELECT energy_type, COUNT(*) AS n FROM measure WHERE rel_obj_no LIKE 'D%' GROUP BY 1"
    monkeypatch.setattr(server, "agent_sql_plan", lambda *_a, **_kw: {"analyses": [{"title": "按能源", "sql": sql}]})
    monkeypatch.setattr(server, "_match_qa_skill", lambda _q: None)
    monkeypatch.setattr(server, "narrative_llm", lambda *_a, **_kw: "ok")
    server._QA_CACHE.clear()
    client = server.app.test_client()
    data = client.post("/api/chat", json={"q": "计量关系按能源类型统计", "graphs": ["built_x"], "nocache": True}).get_json()
    assert {r["energy_type"]: r["n"] for r in data["results"][0]["data"]["rows"]} == {"2": 40}
    assert any(s["step"] == "data_clean" and "清洗规则生效" in s["info"] for s in data["steps"])
    # 关闭清洗:按原始数据作答
    raw = client.post("/api/chat", json={"q": "计量关系按能源类型统计", "graphs": ["built_x"], "nocache": True,
                                         "clean": False}).get_json()
    assert {r["energy_type"]: r["n"] for r in raw["results"][0]["data"]["rows"]} == {"2": 37, "3": 3}
    stream = client.post("/api/chat/stream", json={"q": "计量关系按能源类型统计", "graphs": ["built_x"],
                                                   "nocache": True}).get_data(as_text=True)
    assert "清洗规则生效" in stream


@pytest.fixture
def work(tmp_path, monkeypatch):
    w = tmp_path / "work"; w.mkdir()
    monkeypatch.setattr(server, "WORK", str(w))
    monkeypatch.setattr(server, "_BUILD_CONN_F", str(w / "build_connections.json"))
    return w


def _events(body):
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]


def test_build_runs_cross_check_only_when_the_skill_is_selected(make_sqlite, work, monkeypatch):
    db = _fixture(make_sqlite)
    (work / "build_connections.json").write_text(json.dumps(
        [{"id": "conn_f", "name": "夹具库", "kind": "sqlite", "path": db}]), encoding="utf-8")
    monkeypatch.setattr(server, "_llm_extract_ontology", lambda *a, **k: None)
    monkeypatch.setattr(server, "_build_intent", lambda *a, **k: {})
    monkeypatch.setattr(server, "_build_summary", lambda *a, **k: "摘要")
    client = server.app.test_client()

    def build(skills):
        body = client.post("/api/build/inquire", json={"q": "构建计量本体", "source": "conn_f", "name": "夹具",
                                                       "skills": skills}).get_data(as_text=True)
        evs = _events(body)
        done = next(e for e in evs if e.get("type") == "done")
        steps = {e["step"]: e for e in evs if e.get("type") == "step"}
        return done, steps

    done, steps = build(["ontology-semi-auto", "ontology-cross-check"])
    assert steps["cross_check"]["ok"] and "交叉复核" in steps["cross_check"]["info"]
    assert "数据清洗规则" in steps["data_clean"]["info"]
    ir = server.load_ir(done["graph_key"])
    assert "cross_check" in ir["scenario"]
    done2, steps2 = build(["ontology-semi-auto"])
    assert "cross_check" not in steps2
    assert "cross_check" not in (server.load_ir(done2["graph_key"]).get("scenario") or {})


def test_ui_selects_cross_check_by_default():
    html = open(os.path.join(server.HERE, "ui", "index.html"), encoding="utf-8").read()
    assert "skills:new Set(['ontology-semi-auto','ontology-build-method','ontology-cross-check'])" in html
