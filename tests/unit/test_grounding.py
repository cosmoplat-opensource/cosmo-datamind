"""DR-093: data-grounded naming and term grounding."""
import sqlite3

import grounding as G


def _db():
    con = sqlite3.connect(":memory:")
    con.executescript("""
    CREATE TABLE tb001 (cl0001 TEXT PRIMARY KEY, cl0002 TEXT, cl0003 INTEGER);
    INSERT INTO tb001 VALUES ('a','Alameda',1),('b','Fresno',0),('c','Alameda',1),('d','Los Angeles',0);
    CREATE TABLE tb002 (cl0001 TEXT, cl0004 REAL);
    INSERT INTO tb002 VALUES ('a',10.5),('b',3.0);
    """)
    return con


def test_profile_has_types_distinct_and_common_values():
    p = G.profile_db(_db())
    c = p["tb001"]["cols"]["cl0002"]
    assert p["tb001"]["rows"] == 4 and c["distinct"] == 3 and c["top"][0] == ["Alameda", 2]
    assert "Alameda" in G.profile_brief(p)


def test_evidence_clause_becomes_verified_grounding_with_filter():
    con = _db()
    wl = ["How many magnet schools?\nEvidence: magnet school means cl0003 = 1; county refers to cl0002"]
    eg = G.evidence_groundings(con, wl, G.profile_db(con))
    by = {g["term"]: g for g in eg["groundings"]}
    assert by["magnet school"]["column"] == "cl0003" and by["magnet school"]["rows"] == 2
    assert by["county"]["kind"] == "attribute" and by["county"]["table"] == "tb001"


def test_evidence_filter_hitting_no_rows_is_rejected():
    con = _db()
    eg = G.evidence_groundings(con, ["Q\nEvidence: closed school means cl0003 = 7"], G.profile_db(con))
    assert not eg["groundings"] and eg["rejected"][0]["why"] == "0 rows"


def test_shared_key_code_grounds_in_every_table_that_has_it():
    con = _db()
    eg = G.evidence_groundings(con, ["Q\nEvidence: school code refers to cl0001"], G.profile_db(con))
    assert {g["table"] for g in eg["groundings"]} == {"tb001", "tb002"}


def test_value_lookup_finds_the_column_holding_a_question_literal():
    con = _db()
    prof = G.profile_db(con)
    vg = G.value_groundings(con, ["How many schools are in Fresno?"], G.value_index(con, prof))
    assert [(v["value"], v["table"], v["column"], v["rows"]) for v in vg] == [("Fresno", "tb001", "cl0002", 1)]


def test_names_need_a_basis_found_in_the_data():
    con = _db()
    prof = G.profile_db(con)
    ir = {"objects": [{"name": "tb001", "cn": "tb001", "table": "tb001", "attrs": [{"col": "cl0002", "cn": ""}]},
                      {"name": "tb002", "cn": "学校名录", "table": "tb002", "attrs": []}], "relations": []}
    reply = ('{"tables":[{"table":"tb001","name_en":"School","name_cn":"学校","basis":"values \'Alameda\',\'Fresno\' in cl0002",'
             '"columns":[{"column":"cl0002","name_en":"County","basis":"\'Los Angeles\'"}]}]}')
    bad = '{"tables":[{"table":"tb001","name_en":"Hospital","basis":"values \'St Mary\'"}]}'
    import json
    r = G.name_objects(ir, prof, [], [], lambda _p: reply, json.loads)
    assert r["tables_named"] == 1 and ir["objects"][0]["cn"] == "学校" and ir["objects"][0]["cn_status"] == "candidate"
    assert ir["objects"][0]["attrs"][0]["label_candidate"]["en"] == "County"
    assert r["tables_considered"] == 1                      # dictionary-like name 学校名录 is not overwritten
    ir2 = {"objects": [{"name": "tb001", "cn": "tb001", "table": "tb001", "attrs": []}], "relations": []}
    r2 = G.name_objects(ir2, prof, [], [], lambda _p: bad, json.loads)
    assert r2["tables_named"] == 0 and r2["rejected"][0]["why"] == "basis not found in data"


def test_value_meanings_from_evidence_are_checked_on_the_data():
    con = _db()
    prof = G.profile_db(con)
    wl = ["Q\nEvidence: cl0003 = 1 stands for magnet school; cl0003 = 9 means closed",
          "Q2\nEvidence: 'Fresno' refers to the county seat; county refers to cl0002"]
    eg = G.evidence_groundings(con, wl, prof)
    vs = G.value_semantics(con, wl, prof, eg["groundings"], G.value_index(con, prof))
    got = {(v["column"], v["value"], v["meaning"]) for v in vs}
    assert ("cl0003", 1, "magnet school") in got
    assert not any(v["value"] == 9 for v in vs)                    # 9 does not occur in the data
    assert ("cl0002", "Fresno", "the county seat") in got          # column found from the same evidence


def test_generic_name_detection_does_not_touch_real_names():
    assert G.needs_name({"table": "t", "cn": "主业务实体记录"}) and G.needs_name({"table": "t", "cn": "业务实体分类关联记录"})
    assert G.needs_name({"table": "tb001", "cn": "tb001"})
    assert not G.needs_name({"table": "t", "cn": "业务员信息"}) and not G.needs_name({"table": "t", "cn": "销售订单"})


def test_table_codes_and_bare_numbers_are_not_a_basis():
    allowed = {"alameda", "2014", "fresno"}
    assert not G._basis_ok("linked to tb003 via cl0001", allowed)
    assert not G._basis_ok("'tb003'", allowed)
    assert not G._basis_ok("range up to 2014", allowed)
    assert G._basis_ok("values 'Alameda'", allowed)
