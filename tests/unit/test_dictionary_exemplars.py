"""外部数据字典(DR-083)与已验证问答示例(DR-084)进入问数上下文。

库内没有注释、字段说明另有文件时(BIRD 的 database_description、企业的数据字典表格),按库文件登记后,
问数上下文的列清单带上字段说明与取值含义;同一数据源上已验证的问答示例按与问题的相近程度取前几条供规划参考,
与当前问题完全相同的示例不取。"""
from __future__ import annotations

import json
import os

import server


def _register(tmp_path, monkeypatch, db):
    monkeypatch.setattr(server, "WORK", str(tmp_path))
    monkeypatch.setattr(server, "_qa_db", lambda: db)
    server._EXT_DICT_CACHE.clear()
    server._EXEMPLAR_CACHE.clear()
    d = tmp_path / "dictionaries"
    d.mkdir()
    (d / "s.json").write_text(json.dumps({"tables": {"schools": {
        "DOC": {"desc": "District Ownership Code", "values": "31: State Special Schools; 52: Elementary School District"}}}}))
    (d / "index.json").write_text(json.dumps({os.path.realpath(db): "s.json"}))
    x = tmp_path / "exemplars"
    x.mkdir()
    rows = [{"question": "How many state special schools are there?", "sql": "SELECT COUNT(*) FROM schools WHERE DOC = 31"},
            {"question": "List schools in Fresno county", "sql": "SELECT School FROM schools WHERE County = 'Fresno'"},
            {"question": "Average SAT reading score", "sql": "SELECT AVG(AvgScrRead) FROM satscores"}]
    (x / "s.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (x / "index.json").write_text(json.dumps({os.path.realpath(db): "s.jsonl"}))
    monkeypatch.delenv("DATAMIND_EXEMPLARS_DIR", raising=False)


def test_column_line_carries_description_and_value_meaning(tmp_path, monkeypatch, make_sqlite):
    db = make_sqlite({"schools": ("CDSCode TEXT PRIMARY KEY, School TEXT, DOC TEXT", [("1", "A", "31"), ("2", "B", "52")])})
    _register(tmp_path, monkeypatch, db)
    text = server._obj_cols_text({"table": "schools", "attrs": [{"col": "DOC"}, {"col": "School"}]}, question="state special")
    assert "District Ownership Code" in text and "31: State Special Schools" in text


def test_exemplars_ranked_by_similarity_and_exclude_the_same_question(tmp_path, monkeypatch, make_sqlite):
    db = make_sqlite({"schools": ("CDSCode TEXT PRIMARY KEY, DOC TEXT", [("1", "31")])})
    _register(tmp_path, monkeypatch, db)
    got = server._qa_exemplars("How many state special schools are in Fresno?", k=2)
    assert got and got[0]["sql"].endswith("DOC = 31")
    same = server._qa_exemplars("How many state special schools are there?", k=3)
    assert all(r["question"] != "How many state special schools are there?" for r in same)
    text, n = server._exemplar_text("How many state special schools are in Fresno?")
    assert n >= 1 and "相似问题的已验证查询" in text


def test_nothing_registered_means_no_dictionary_and_no_exemplars(tmp_path, monkeypatch, make_sqlite):
    db = make_sqlite({"t": ("id INTEGER PRIMARY KEY", [(1,)])})
    monkeypatch.setattr(server, "WORK", str(tmp_path))
    monkeypatch.setattr(server, "_qa_db", lambda: db)
    server._EXT_DICT_CACHE.clear()
    server._EXEMPLAR_CACHE.clear()
    monkeypatch.delenv("DATAMIND_EXEMPLARS_DIR", raising=False)
    assert server._ext_dict("t") == {} and server._qa_exemplars("anything") == []
