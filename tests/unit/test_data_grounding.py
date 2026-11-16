"""按数据命名与术语接地(DR-136,移植自对比实验分支):取值画像、问题用语 → 列、按取值反查、按画像起候选名。"""
import sqlite3

import data_grounding as G


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


# ── 产品接入(DR-136):中文的口径说明、概念记录的形状、构建与问数 ─────────────────────────────

def test_chinese_evidence_clauses_are_grounded_too():
    con = _db()
    wl = ["有多少所磁石学校?\n说明:磁石学校指 cl0003 = 1；县名对应 cl0002",
          "阿拉米达县有几所学校？口径：「阿拉米达」即 cl0002 = 'Alameda'"]
    eg = G.evidence_groundings(con, wl, G.profile_db(con))
    by = {g["term"]: g for g in eg["groundings"]}
    assert by["磁石学校"]["filter"] == '"cl0003" = 1' and by["磁石学校"]["rows"] == 2
    assert by["县名"]["column"] == "cl0002" and by["县名"]["kind"] == "attribute"
    assert by["阿拉米达"]["rows"] == 2


def test_grounded_terms_become_candidate_concepts_in_the_product_shape():
    con = _db()
    eg = G.evidence_groundings(con, ["Q\n说明:磁石学校指 cl0003 = 1"], G.profile_db(con))
    (c,) = G.as_concepts(eg["groundings"])
    assert c["status"] == "candidate" and c["semantic_status"] == "not_reviewed" and c["origin"] == "evidence_grounding"
    assert (c["name"], c["table"], c["column"], c["filter"]) == ("磁石学校", "tb001", "cl0003", '"cl0003" = 1')
    assert c["evidence"]["query_verified"] is True and c["evidence"]["rows"] == 2 and "cl0003" in c["evidence"]["clause"]


def test_the_build_step_profiles_grounds_and_names_opaque_tables(make_sqlite):
    import json
    import server
    db = make_sqlite({"tb001": ("cl0001 TEXT, cl0002 TEXT, cl0003 INTEGER",
                                [("a", "Alameda", 1), ("b", "Fresno", 0), ("c", "Alameda", 1), ("d", "Los Angeles", 0)])})
    ir = {"objects": [{"name": "tb001", "cn": "tb001", "table": "tb001", "attrs": [{"col": "cl0002"}]}], "relations": []}
    reply = ('{"tables":[{"table":"tb001","name_en":"School","name_cn":"学校","basis":"values \'Alameda\',\'Fresno\' in cl0002",'
             '"columns":[{"column":"cl0002","name_en":"County","basis":"\'Los Angeles\'"}]}]}')
    out = server._data_grounding(db, ir, ["有多少所磁石学校?\n说明:磁石学校指 cl0003 = 1"], ask=lambda _p: reply)
    assert out["naming"]["tables_named"] == 1 and ir["objects"][0]["cn"] == "学校" and ir["objects"][0]["cn_status"] == "candidate"
    assert [c["name"] for c in out["concepts"]] == ["磁石学校"] and "tb001〔学校〕 (4 rows)" in out["brief"]
    assert ir["term_groundings"]["evidence"][0]["term"] == "磁石学校"
    json.dumps(ir)                                               # 写进本体文件的内容可以序列化


def test_without_opaque_names_or_questions_the_step_does_not_call_the_model(make_sqlite):
    import server
    db = make_sqlite({"orders": ("order_id TEXT, amount REAL", [("a", 1.0)])})
    ir = {"objects": [{"name": "orders", "cn": "销售订单", "table": "orders", "attrs": []}], "relations": []}
    calls = []
    out = server._data_grounding(db, ir, [], ask=lambda p: calls.append(p) or "{}")
    assert calls == [] and out["naming"]["tables_considered"] == 0 and out["concepts"] == []


def test_the_concept_step_receives_the_profile_instead_of_the_bare_column_list(make_sqlite):
    import workload_concepts
    db = make_sqlite({"tb001": ("cl0001 TEXT, cl0003 INTEGER", [("a", 1), ("b", 0)])})
    seen = []
    workload_concepts.build(sqlite3.connect(db), {"objects": [{"table": "tb001"}]}, {}, ["有多少所磁石学校?"],
                            lambda p: seen.append(p) or "{}", lambda s: {}, brief="PROFILE-BRIEF-MARKER")
    assert seen and "PROFILE-BRIEF-MARKER" in seen[0]


# ── DR-138:未通过数据核验的概念,带原因和真实结构重提(至多 2 轮) ─────────────────────────────────

def _concept_db(make_sqlite):
    return sqlite3.connect(make_sqlite({"tb001": ("cl0001 TEXT, cl0003 INTEGER", [("a", 1), ("b", 0), ("c", 1)])}))


def test_a_concept_that_fails_verification_is_proposed_again_with_the_reason(make_sqlite):
    import json
    import workload_concepts
    con = _concept_db(make_sqlite)
    replies = [json.dumps({"concepts": [{"name": "磁石学校", "table": "tb001", "column": "magnet", "filter": ""}]}),
               json.dumps({"concepts": [{"name": "磁石学校", "table": "tb001", "column": "cl0003", "filter": "cl0003 = 1"}]})]
    prompts = []

    def ask(p):
        prompts.append(p)
        return replies[min(len(prompts), len(replies)) - 1]
    repair = {"tables_brief": lambda tables: "- tb001 (3 rows): cl0003[integer; 2 distinct; e.g. 1, 0]",
              "groundings": [{"term": "磁石学校", "table": "tb001", "column": "cl0003", "filter": "cl0003 = 1"}]}
    out = workload_concepts.build(con, {"objects": [{"table": "tb001"}]}, {}, ["有多少所磁石学校?"], ask, json.loads,
                                  brief="BRIEF", repair=repair)
    assert len(prompts) == 2 and "name_or_column_missing" in prompts[1] and "cl0003[integer" in prompts[1]
    (c,) = out["concepts"]
    assert c["column"] == "cl0003" and c["repaired_round"] == 1
    assert out["repair"] == {"rounds": 1, "resubmitted": 1, "recovered": 1}


def test_repair_stops_after_two_rounds_and_is_off_without_the_profile(make_sqlite):
    import json
    import workload_concepts
    con = _concept_db(make_sqlite)
    bad = json.dumps({"concepts": [{"name": "x", "table": "tb001", "column": "nope", "filter": ""}]})
    calls = []
    repair = {"tables_brief": lambda tables: "brief", "groundings": []}
    out = workload_concepts.build(con, {"objects": [{"table": "tb001"}]}, {}, ["q"], lambda p: calls.append(p) or bad,
                                  json.loads, brief="BRIEF", repair=repair)
    assert len(calls) == 3 and out["repair"]["rounds"] == 2 and out["concepts"] == []
    calls.clear()
    workload_concepts.build(con, {"objects": [{"table": "tb001"}]}, {}, ["q"], lambda p: calls.append(p) or bad, json.loads)
    assert len(calls) == 1


def test_a_repair_round_cannot_add_more_concepts_than_failed(make_sqlite):
    import json
    import workload_concepts
    con = sqlite3.connect(make_sqlite({"tb001": ("cl0001 TEXT, cl0003 INTEGER, cl0004 INTEGER",
                                                 [("a", 1, 2), ("b", 0, 3), ("c", 1, 4)])}))
    first = json.dumps({"concepts": [{"name": "x", "table": "tb001", "column": "nope", "filter": ""}]})
    many = json.dumps({"concepts": [{"name": "x", "table": "tb001", "column": "cl0003", "filter": ""},
                                    {"name": "y", "table": "tb001", "column": "cl0004", "filter": ""},
                                    {"name": "z", "table": "tb001", "column": "cl0001", "filter": ""}]})
    calls = []

    def ask(p):
        calls.append(p)
        return first if len(calls) == 1 else many
    out = workload_concepts.build(con, {"objects": [{"table": "tb001"}]}, {}, ["q"], ask, json.loads, brief="B",
                                  repair={"tables_brief": lambda t: "b", "groundings": []})
    assert out["repair"]["recovered"] == 1 and out["dropped"] >= 0 and out["accepted"] <= out["proposed"]
