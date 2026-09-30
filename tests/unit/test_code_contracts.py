# -*- coding: utf-8 -*-
"""DR-065 代码契约与问数执行库:两个场景共同缺的两块。

- 代码契约:两种场景最强的证据都以 zip / docx 交付(后端数据访问层 SQL、前端代码、接口文档),
  平台原先不读这两种格式。现在内存中读取、去掉内网地址,抽出连接、码表、接口与字典契约,
  按业务词根对齐到库表;连接契约在裁决中替代命名证据(数据判定条件不变)。
- 问数执行库:用登记的 SQLite 数据源(抽样库)建的本体,问数就在该库上执行。
"""
import io
import json
import zipfile

import pytest

import code_contracts as cc

MAPPER = """<mapper namespace="x">
<select id="trace" resultType="Vo">
  SELECT t.wd_sn, t4.zone_name FROM t_pass_record t
  LEFT JOIN public.sy_dept f on f.id = t.data_auth
  <include refid="BaseMapper.tenementSql3"><property name="join_table" value="f"/></include>
  LEFT JOIN t_line_zone t4 ON t4.ZONE_ID = t.wd_zone_sn and t4.tenement_id = t.tenement_id
  LEFT JOIN sys_dict_item a ON a.code = t.WD_DEFECT_FLAG AND a.dict_code = 'DEFECT_FLAG'
  LEFT JOIN sys_dict_item b ON b.code = t.wd_lane AND b.dict_code = 'LANE_TYPE'
  <where> t.wd_sn = #{sn} </where>
</select></mapper>"""

VUE = """export function energyDict(sysNo) {
  return service({ url: `${emsUrl}/api/system/${sysNo}/energyKind`, method: "get" });
}
const q = { dictCode: "energyKind" };
// url: `https://10.1.2.3:8080/ems2/dashboard/energy/analysis`
"""


def _zip(members, utf8=True, monkeypatch=None):
    buf = io.BytesIO()
    if not utf8:
        # 模拟 Windows 中文环境打包:文件名按 GBK 写入,且不声明 UTF-8 标志
        monkeypatch.setattr(zipfile.ZipInfo, "_encodeFilenameFlags",
                            lambda self: (self.filename.encode("gbk"), self.flag_bits))
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(zipfile.ZipInfo(name), data)
    return buf.getvalue()


class TestReading:
    def test_zip_reads_code_members_only_and_scrubs_addresses(self):
        raw = _zip({"src/a.vue": VUE, "img/logo.png": b"\x89PNG....", "mapper/X.xml": MAPPER,
                    "cfg/app.yml": "url: jdbc:mysql://10.9.8.7:3306/db\n"})
        text = cc.read_zip(raw)
        assert "### 文件 src/a.vue" in text and "### 文件 mapper/X.xml" in text and "logo.png" not in text
        assert "10.1.2.3" not in text and "10.9.8.7" not in text and "[地址]" in text
        assert "/ems2/dashboard/energy/analysis" in text          # 接口路径保留

    def test_gbk_member_names_are_decoded(self, monkeypatch):
        text = cc.read_zip(_zip({"后端/映射.xml": MAPPER}, utf8=False, monkeypatch=monkeypatch))
        assert "### 文件 后端/映射.xml" in text

    def test_oversized_members_are_skipped(self, monkeypatch):
        monkeypatch.setattr(cc, "MEMBER_CAP", 10)
        assert cc.read_zip(_zip({"a.sql": "select 1 from t join u on t.a = u.b"})) == ""

    def test_docx_tables_rows_and_field_codes(self):
        body = ('<w:document><w:body><w:p><w:r><w:t>接口文档</w:t></w:r></w:p>'
                '<w:p><w:r><w:instrText>HYPERLINK "http://10.0.0.1/x"</w:instrText></w:r><w:r><w:t>测试环境</w:t></w:r></w:p>'
                '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>参数</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>说明</w:t></w:r></w:p></w:tc></w:tr>'
                '<w:tr><w:tc><w:p><w:r><w:t>start</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>开始时间</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
                '</w:body></w:document>')
        text = cc.read_docx(_zip({"word/document.xml": body}))
        assert "HYPERLINK" not in text and "10.0.0.1" not in text
        assert "参数 | 说明" in text.replace("  ", " ") and "start | 开始时间" in text.replace("  ", " ")


class TestExtract:
    def test_mybatis_joins_codebooks_and_envelope(self):
        res = cc.extract([("代码.zip", "### 文件 X.xml\n" + MAPPER)])
        pairs = {(j["a"][0], j["a"][1], j["b"][0], j["b"][1]): j for j in res["joins"]}
        assert ("t_line_zone", "ZONE_ID", "t_pass_record", "wd_zone_sn") in pairs
        env = [j for j in res["joins"] if j["envelope"]]
        assert env and env[0]["a"][1] == "tenement_id"
        books = {(b["column"], b["group"]) for b in res["codebooks"]}
        assert books == {("WD_DEFECT_FLAG", "DEFECT_FLAG"), ("wd_lane", "LANE_TYPE")}   # 按子句配对,不交叉

    def test_frontend_apis_and_dicts(self):
        res = cc.extract([("看板.zip", "### 文件 src/a.vue\n" + VUE)])
        assert "/api/system/${sysNo}/energyKind" in [a["path"] for a in res["apis"]]
        assert res["dicts"] == [{"group": "energyKind", "file": "看板.zip:src/a.vue"}]

    def test_resolve_to_lakehouse_names_and_prefixed_columns(self):
        res = cc.extract([("代码.zip", "### 文件 X.xml\n" + MAPPER)])
        tab_cols = {"dim_line_zone": ["zone_id", "zone_name"], "dwd_f1_pass_record": ["wd_sn", "zone_sn", "track"],
                    "dim_sys_dict_item": ["dict_code", "code", "name"]}
        got = {(r["a"][0], r["a"][1], r["b"][0], r["b"][1]) for r in cc.resolve(res, tab_cols)}
        assert ("dim_line_zone", "zone_id", "dwd_f1_pass_record", "zone_sn") in got
        assert cc.hit(cc.resolve(res, tab_cols), "dwd_f1_pass_record", "zone_sn", "dim_line_zone", "zone_id")
        assert not any("tenement" in str(x) for x in got)                                  # 信封连接不算
        text = cc.text_for_prompt(res, cc.resolve(res, tab_cols))
        assert "dim_line_zone.zone_id" in text and "DEFECT_FLAG" in text


server = pytest.importorskip("server")


def _lake_db(make_sqlite):
    return make_sqlite({
        "dim_line_zone": ("zone_id TEXT, zone_name TEXT", [(f"A{i}", f"工位{i}") for i in range(20)]),
        "dwd_f1_pass_record": ("wd_sn TEXT, zone_sn TEXT", [(f"S{i}", f"A{i % 20}") for i in range(200)]),
    })


def test_contract_lets_a_differently_named_key_pass_the_name_gate(make_sqlite, tmp_path, monkeypatch):
    """zone_sn → zone_id:数据完全对得上,只因键名不同未通过命名条件;代码里就这么连,契约替代命名证据。"""
    monkeypatch.setattr(server, "WORK", str(tmp_path))
    db = _lake_db(make_sqlite)
    extracted = {"objects": [{"name": "过站", "table": "dwd_f1_pass_record", "kind": "event"},
                             {"name": "区域", "table": "dim_line_zone", "kind": "object"}],
                 "relations": [{"source": "过站", "target": "区域", "verb": "发生于"}]}
    ev = server._gather_evidence(db)
    plain = server._adjudicate_ir(db, "无契约", json.loads(json.dumps(extracted)), ev)
    assert [r["status"] for r in plain["relations"]] == ["candidate"]
    res = cc.extract([("代码.zip", "### 文件 X.xml\n" + MAPPER)])
    ev["contracts"], ev["contracts_resolved"] = res, cc.resolve(res, ev["tab_cols"])
    withc = server._adjudicate_ir(db, "有契约", json.loads(json.dumps(extracted)), ev)
    rel = withc["relations"][0]
    assert rel["status"] == "verified" and "代码契约" in rel["note"]
    assert rel["evidence"]["contract"]["code"].startswith("t_line_zone.ZONE_ID")


def test_uploaded_zip_and_docx_become_evidence(make_sqlite, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "WORK", str(tmp_path))
    (tmp_path / "uploads_code.zip").write_bytes(_zip({"X.xml": MAPPER}))
    db = _lake_db(make_sqlite)
    ev = server._gather_evidence(db)
    assert any(f["name"] == "code.zip" and f["chunks"] for f in ev["files"])
    assert ev["contracts_resolved"] and ev["contracts_resolved"][0]["file"].endswith("X.xml")


def test_qa_runs_on_the_ontologys_source_database(make_sqlite, tmp_path, monkeypatch):
    """用登记的 SQLite 数据源建的本体:图谱列表判为可问数,问数在该库上执行。"""
    db = _lake_db(make_sqlite)
    monkeypatch.setattr(server, "WORK", str(tmp_path))
    monkeypatch.setattr(server, "_BUILD_CONN_F", str(tmp_path / "build_connections.json"))
    (tmp_path / "build_connections.json").write_text(json.dumps([{"id": "conn_l", "name": "湖仓抽样", "kind": "sqlite",
                                                                  "path": db}]), encoding="utf-8")
    ir = {"objects": [{"name": "过站", "table": "dwd_f1_pass_record"}, {"name": "区域", "table": "dim_line_zone"}],
          "relations": [{"source_concept": "过站", "target_concept": "区域", "verb": "发生于", "status": "verified",
                         "evidence": {"child_key": "zone_sn", "parent_key": "zone_id"}}],
          "build_manifest": {"source": {"id": "conn_l", "name": "湖仓抽样"}}}
    (tmp_path / "built_lake01.json").write_text(json.dumps(ir, ensure_ascii=False), encoding="utf-8")
    server._GRAPH_ROW_CACHE.clear()
    client = server.app.test_client()
    row = next(g for g in client.get("/api/graphs").get_json() if g["id"] == "built_lake01")
    assert row["queryable"] is True
    sql = "SELECT a.zone_name AS 区域, COUNT(*) AS n FROM dwd_f1_pass_record w JOIN dim_line_zone a ON a.zone_id = w.zone_sn GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 1"
    monkeypatch.setattr(server, "agent_sql_plan", lambda *a, **k: {"analyses": [{"title": "过站排行", "sql": sql}]})
    monkeypatch.setattr(server, "_match_qa_skill", lambda _q: None)
    monkeypatch.setattr(server, "narrative_llm", lambda *a, **k: "ok")
    server._QA_CACHE.clear()
    data = client.post("/api/chat", json={"q": "各工位过站排行", "graphs": ["built_lake01"], "nocache": True}).get_json()
    assert data["results"][0]["data"]["rows"] == [{"区域": "工位0", "n": 10}]
    assert "湖仓抽样" in data["steps"][0]["info"]
    assert server._QA_DB.get() is None                                   # 请求结束即复位
    stream = client.post("/api/chat/stream", json={"q": "各工位过站排行", "graphs": ["built_lake01"],
                                                   "nocache": True}).get_data(as_text=True)
    assert "湖仓抽样" in stream and "工位0" in stream


def test_mixed_sources_fall_back_to_main_database(monkeypatch):
    irs = {"built_a": {"build_manifest": {"source": {"id": "conn_a"}}},
           "built_b": {"build_manifest": {"source": {"id": "demo"}}}}
    monkeypatch.setattr(server, "load_ir_edited", lambda k: irs.get(k))
    monkeypatch.setattr(server, "_load_conns", lambda: [{"id": "conn_a", "kind": "sqlite", "path": __file__}])
    assert server._qa_source_for(["built_a"]) == __file__
    assert server._qa_source_for(["built_a", "built_b"]) is None


def _cc_module():
    import importlib.util
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                        "skills_seed", "ontology-cross-check", "scripts", "cross_check.py")
    spec = importlib.util.spec_from_file_location("cc_scope", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _cand(src, dst, ck, pk):
    return {"source_concept": src, "target_concept": dst, "status": "candidate",
            "evidence": {"child_key": ck, "parent_key": pk, "overlap": 100.0, "parent_unique": False, "name_ok": True}}


class TestValidRowsAcrossScenarios:
    """父键不唯一的四种成因,两种场景各占几种;先定有效行再判唯一,通过后升为已验证并保留原裁决。"""

    def test_tenant_scoped_keys(self, make_sqlite):
        xc = _cc_module()
        db = make_sqlite({
            "area": ("zone_id TEXT, zone_name TEXT, factory_code TEXT",
                     [(f"A{i}", f"{f}工位{i}", f) for f in ("F1", "F2") for i in range(10)]),
            "wip": ("sn TEXT, zone_sn TEXT, factory_code TEXT", [(f"S{i}", f"A{i % 10}", "F1") for i in range(50)]),
        })
        ir = {"objects": [{"name": "wip", "table": "wip"}, {"name": "area", "table": "area"}],
              "relations": [_cand("wip", "area", "zone_sn", "zone_id")]}
        rows, _ = xc.check(ir, xc.Db(db))
        vr = rows[0]["channels"]["valid_rows"]
        assert rows[0]["verdict"] == "valid_rows_candidate" and vr["rule"] == "tenant_scoped"
        out = xc.apply(json.loads(json.dumps(ir)), rows)
        assert out["relations"][0]["status"] == "verified"
        assert out["relations"][0]["evidence"]["cross_check"]["prev_status"] == "candidate"

    def test_dictionary_codes_scoped_by_code_contract(self, make_sqlite):
        xc = _cc_module()
        db = make_sqlite({
            "dict_val": ("dict_code TEXT, code TEXT, name TEXT",
                         [("TRACK", "1", "单轨"), ("TRACK", "2", "双轨"), ("FACE", "1", "正面"), ("FACE", "2", "反面")]),
            "wip": ("sn TEXT, track TEXT", [(f"S{i}", str(1 + i % 2)) for i in range(20)]),
        })
        ir = {"objects": [{"name": "wip", "table": "wip"}, {"name": "dv", "table": "dict_val"}],
              "relations": [_cand("wip", "dv", "track", "code")],
              "scenario": {"code_contracts": {"codebooks": [{"table": "wip", "column": "track", "dictionary": "dict_val",
                                                             "group_column": "dict_code", "group": "TRACK"}]}}}
        rows, _ = xc.check(ir, xc.Db(db))
        assert rows[0]["channels"]["valid_rows"]["rule"] == "scoped"
        ir.pop("scenario")                                   # 没有码表契约就不猜分组
        assert xc.check(ir, xc.Db(db))[0][0]["verdict"] == "unchanged"

    def test_versions_with_stable_names_are_kept_latest(self, make_sqlite):
        xc = _cc_module()
        db = make_sqlite({
            "grp": ("group_code TEXT, group_name TEXT, insert_time TEXT",
                    [(f"G{i}", f"工序{i}", f"2024-0{1 + v}-01") for i in range(10) for v in range(3)]),
            "wip": ("sn TEXT, group_code TEXT", [(f"S{i}", f"G{i % 10}") for i in range(30)]),
        })
        ir = {"objects": [{"name": "wip", "table": "wip"}, {"name": "grp", "table": "grp"}],
              "relations": [_cand("wip", "grp", "group_code", "group_code")]}
        vr = xc.check(ir, xc.Db(db))[0][0]["channels"]["valid_rows"]
        assert vr["rule"] == "keep_latest" and vr["stable_name_share"] == 1.0

    def test_unnamed_candidates_are_not_upgraded(self, make_sqlite):
        """有效行成立但键名无据(也没有代码契约):只送复核,不升级。"""
        xc = _cc_module()
        db = make_sqlite({"p": ("k TEXT, is_del INTEGER", [("a", 0), ("a", 1), ("b", 0)]),
                          "c": ("x TEXT", [("a",), ("b",)])})
        rel = _cand("c", "p", "x", "k")
        rel["evidence"]["name_ok"] = False
        ir = {"objects": [{"name": "c", "table": "c"}, {"name": "p", "table": "p"}], "relations": [rel]}
        rows, _ = xc.check(ir, xc.Db(db))
        assert rows[0]["verdict"] == "valid_rows_candidate"
        assert xc.apply(json.loads(json.dumps(ir)), rows)["relations"][0]["status"] == "candidate"


def test_fallback_build_stays_in_round_scope(make_sqlite, tmp_path):
    import quick_build
    db = make_sqlite({"a": ("id INTEGER PRIMARY KEY, b_id INTEGER", [(1, 1)]), "b": ("id INTEGER PRIMARY KEY", [(1,)]),
                      "c": ("id INTEGER PRIMARY KEY", [(1,)]),
                      "_dm_tables": ("table_name TEXT, source_table TEXT, comment TEXT, rows_est INTEGER", [])})
    out = tmp_path / "o.json"
    ir = quick_build.build(db, str(out), "范围", None, ["a", "b"])
    assert sorted(o["tables"][0] for o in ir["objects"]) == ["a", "b"]
    ir_all = quick_build.build(db, str(out), "全库")
    assert "_dm_tables" not in [o["tables"][0] for o in ir_all["objects"]]


def test_acceptance_accepts_parent_uniqueness_on_valid_rows_only_with_a_named_rule():
    import build_quality
    base = {"objects": [{"name": "a", "table": "a"}, {"name": "b", "table": "b"}]}
    ev = {"child_key": "k", "parent_key": "k", "overlap": 100.0, "parent_unique": False, "name_ok": True}
    ok = dict(base, relations=[{"source_concept": "a", "target_concept": "b", "status": "verified",
                                "evidence": dict(ev, valid_rows={"state": "support", "rule": "tenant_scoped"})}])
    bad = dict(base, relations=[{"source_concept": "a", "target_concept": "b", "status": "verified",
                                 "evidence": dict(ev, valid_rows={"state": "support", "rule": "guess"})}])
    kinds = lambda ir: {i["type"] for i in build_quality.evaluate(ir)["evidence"]["issues"]}
    assert "verified_parent_not_unique" not in kinds(ok)
    assert "verified_parent_not_unique" in kinds(bad)


def test_chinese_recall_matches_part_of_an_object_name(make_sqlite, monkeypatch):
    db = make_sqlite({"inst_device": ("dev_no TEXT, dev_type TEXT", [("D1", "EMTR")]),
                      "user_ent": ("ent_no TEXT", [("E1",)])})
    monkeypatch.setattr(server, "DB", db)
    ir = {"objects": [{"name": "device", "cn": "能源计量器具", "table": "inst_device"},
                      {"name": "ent", "cn": "企业", "table": "user_ent"}], "links": []}
    monkeypatch.setattr(server, "_anchor_ir", lambda keys: (json.loads(json.dumps(ir)), ["built_x"], ""))
    trace = {}
    server.build_context("各类计量器具有多少台", trace=trace, graph_keys=["built_x"])
    hit = next(o for o in trace["objects"] if o["table"] == "inst_device")
    assert "计量器" in hit["hits"] or "量器具" in hit["hits"]


def test_child_key_as_qualifier_plus_parent_key_is_name_compatible():
    import dao_core
    assert dao_core.key_name_ok("mete_dev_type_code", "dev_type_code")
    assert dao_core.key_name_ok("meta_sys_type_code", "sys_type_code")
    assert dao_core.key_name_ok("pm_zone_sn", "zone_sn")
    assert not dao_core.key_name_ok("alarm_type", "type_code")
    assert not dao_core.key_name_ok("x_status", "status")          # 泛词不算
    assert not dao_core.key_name_ok("order_id", "id")


def test_tenant_scoped_keys_must_be_joined_with_the_tenant_in_qa():
    """区域编号按工厂复用:只按编号关联会把不同工厂的同号区域串在一起(实测计数虚高)。"""
    ir = {"objects": [{"name": "wip", "table": "wip"}, {"name": "area", "table": "area"}],
          "relations": [{"source_concept": "wip", "target_concept": "area", "status": "verified",
                         "evidence": {"child_key": "zone_sn", "parent_key": "zone_id",
                                      "contract": {"code": "t_line_zone.ZONE_ID = t_pass_record.wd_zone_sn", "file": "X.xml"},
                                      "valid_rows": {"state": "support", "rule": "tenant_scoped",
                                                     "tenant_column": "factory_code"}}}]}
    hints = server._join_hints(ir, ["wip", "area"])
    assert hints and "wip.zone_sn = area.zone_id AND wip.factory_code = area.factory_code" in hints[0]
    ok, why = server._validate_sql_ontology(
        "SELECT a.zone_name, COUNT(*) FROM wip w JOIN area a ON a.zone_id = w.zone_sn GROUP BY 1", ir, strict=True)
    assert not ok and "factory_code" in why
    ok, _ = server._validate_sql_ontology(
        "SELECT a.zone_name, COUNT(*) FROM wip w JOIN area a ON a.zone_id = w.zone_sn AND a.factory_code = w.factory_code "
        "GROUP BY 1", ir, strict=True)
    assert ok


def test_term_contracts_from_sql_comments_and_javadoc():
    xml = """<select id="x">SELECT
        A.wd_zone_sn,                           <!-- 线别ID -->
        A.wd_lane,   -- 上面的那个 left_join 为什么引入这段sql
        B.zone_name AS wd_area_name               <!-- 线别名称 -->
        FROM t_pass_record A LEFT JOIN t_line_zone B ON B.zone_id = A.wd_zone_sn</select>"""
    java = """public class Vo {
    /**
     * 线别代码
     */
    private String weZoneSn;
}"""
    res = cc.extract([("代码.zip", "### 文件 X.xml\n" + xml + "\n### 文件 Vo.java\n" + java)])
    labels = {(x["column"], x["label"]) for x in res["labels"]}
    assert ("wd_zone_sn", "线别ID") in labels and ("we_zone_sn", "线别代码") in labels
    assert not any("为什么" in lab for _, lab in labels)                 # 开发者评注不收
    got = cc.resolve_labels(res, {"dwd_f1_pass_record": ["id", "zone_sn", "track"], "dim_line_zone": ["zone_id", "zone_name"]})
    assert got[("dwd_f1_pass_record", "zone_sn")] == "线别ID"
    assert ("dwd_f1_pass_record", "id") not in got


def test_soft_deleted_catalogs_must_be_filtered_in_qa():
    """类型目录里同一编码有已删除行:只按编码关联会重复计数(实测液体流量计 70 台算成 140 台)。"""
    ir = {"objects": [{"name": "dev", "table": "device"}, {"name": "typ", "table": "device_type"}],
          "relations": [{"source_concept": "dev", "target_concept": "typ", "status": "verified",
                         "evidence": {"child_key": "type_code", "parent_key": "type_code",
                                      "valid_rows": {"state": "support", "rule": "soft_delete", "column": "is_del"}}}]}
    assert "AND device_type.is_del = 0" in server._join_hints(ir, ["device", "device_type"])[0]
    sql = "SELECT t.name, COUNT(*) FROM device d JOIN device_type t ON t.type_code = d.type_code"
    ok, why = server._validate_sql_ontology(sql + " GROUP BY 1", ir, strict=True)
    assert not ok and "is_del" in why
    ok, _ = server._validate_sql_ontology(sql + " AND t.is_del = 0 GROUP BY 1", ir, strict=True)
    assert ok


def test_build_with_uploaded_code_fills_labels_and_records_contracts(make_sqlite, tmp_path, monkeypatch):
    """带代码包构建一轮:契约、字段中文名进入产物,构建流不中断。"""
    w = tmp_path / "work"; w.mkdir()
    monkeypatch.setattr(server, "WORK", str(w))
    monkeypatch.setattr(server, "_BUILD_CONN_F", str(w / "build_connections.json"))
    db = _lake_db(make_sqlite)
    (w / "build_connections.json").write_text(json.dumps([{"id": "conn_l", "name": "湖仓", "kind": "sqlite", "path": db}]),
                                              encoding="utf-8")
    code = MAPPER.replace("t.wd_sn, t4.zone_name", "t.wd_sn, t.wd_zone_sn,   <!-- 线别ID -->\n t4.zone_name")
    (w / "uploads_code.zip").write_bytes(_zip({"X.xml": code}))
    monkeypatch.setattr(server, "_llm_extract_ontology", lambda *a, **k: {
        "objects": [{"name": "过站", "table": "dwd_f1_pass_record", "kind": "event"},
                    {"name": "区域", "table": "dim_line_zone", "kind": "object"}],
        "relations": [{"source": "过站", "target": "区域", "verb": "发生于"}]})
    monkeypatch.setattr(server, "_build_intent", lambda *a, **k: {})
    monkeypatch.setattr(server, "_build_summary", lambda *a, **k: "摘要")
    body = server.app.test_client().post("/api/build/inquire", json={
        "q": "过站本体", "source": "conn_l", "name": "湖仓", "skills": ["ontology-semi-auto"]}).get_data(as_text=True)
    evs = [json.loads(x[6:]) for x in body.splitlines() if x.startswith("data: ")]
    steps = {e["step"]: e for e in evs if e.get("type") == "step"}
    assert "代码契约" in steps["code_contracts"]["info"] and "字段含义" in steps["code_labels"]["info"]
    done = next(e for e in evs if e.get("type") == "done")
    ir = server.load_ir(done["graph_key"])
    wip = next(o for o in ir["objects"] if o["name"] == "过站")
    assert any(a.get("cn") == "线别ID" and a.get("cn_source") == "代码注释" for a in wip.get("attrs", []))
    assert ir["scenario"]["code_contracts"]["joins"]
