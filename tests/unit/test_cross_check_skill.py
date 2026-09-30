# -*- coding: utf-8 -*-
"""多通道交叉复核与数据清洗技能:取值重叠之外的独立证据,以及把编码冲突、标错、悬空引用
变成带证据的清洗规则(源库只读)。

规则来自能碳实库抽样的复核:240 条已验证关系中 62 条是租户键、8 条是自增 id 碰撞;
计量关系指向设备的一侧大量悬空(重叠率只有约 11%),但设备类别仍决定能源类型,
其中电能表有若干行被标成了别的能源。
"""
import importlib.util
import json
import os
import sqlite3
import subprocess

import pytest

import skill_registry

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SKILL = os.path.join(ROOT, "skills_seed", "ontology-cross-check")


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SKILL, "scripts", name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cc = _load("cross_check")


def _fixture(make_sqlite):
    ent = [(i, f"E{i}", f"企业{i}", "2024-01-01 00:00:00") for i in range(1, 31)]
    devices = [(f"D{i}", "EMTR") for i in range(40)] + [(f"F{i}", "FMT") for i in range(12)]
    measure = ([(i, 0, f"D{i}", 3 if i < 3 else 2) for i in range(40)]
               + [(100 + i, 0, f"F{i}", 1) for i in range(12)]
               + [(200 + i, 0, f"GONE{i}", 2) for i in range(160)]
               + [(500 + i, 1, f"S{i}", 2) for i in range(10)])  # 指向系统的一侧:多态引用
    tables = {
        "ent": ("id INTEGER PRIMARY KEY, ent_no TEXT, ent_name TEXT, create_at TEXT", ent),
        "order_a": ("id INTEGER PRIMARY KEY, ent_no TEXT, ent_name TEXT, create_at TEXT",
                    [(i, f"E{i}", f"企业{i}", "2024-02-01 00:00:00") for i in range(1, 31)]),
        "bad_name": ("id INTEGER PRIMARY KEY, ent_id INTEGER, ent_name TEXT",
                     [(i, i, f"别家{i}") for i in range(1, 31)]),
        "stale_name": ("id INTEGER PRIMARY KEY, ent_no TEXT, ent_name TEXT",
                       [(i, f"E{i}", f"旧名{i}") for i in range(1, 31)]),
        "cfg": ("id INTEGER PRIMARY KEY, title TEXT", [(i, "c") for i in range(1, 31)]),
        "log_x": ("id INTEGER PRIMARY KEY, msg TEXT", [(i, "m") for i in range(1, 31)]),
        "sys_dict_item": ("dict_code TEXT, value TEXT, label TEXT",
                          [("energy_type", "1", "水"), ("energy_type", "2", "电"), ("energy_type", "3", "风"),
                           ("energy_type", "4", "蒸汽")]),
        "device": ("dev_no TEXT PRIMARY KEY, dev_type_code TEXT", devices),
        "measure": ("id INTEGER PRIMARY KEY, rel_obj_type INTEGER, rel_obj_no TEXT, energy_type TEXT", measure),
        "price": ("id INTEGER PRIMARY KEY, energy_type TEXT", [(i, str(1 + i % 4)) for i in range(40)]),
        "a__shell": ("id INTEGER", []),
        "b__shell": ("id INTEGER", [(1,)]),
        "_dm_columns": ("table_name TEXT, column_name TEXT, type TEXT, comment TEXT",
                        [("price", "energy_type", "text", "能源类型:1 电、2 水")]),
    }
    for k in range(8):  # 企业编号成为枢纽键
        tables[f"t{k}"] = ("id INTEGER PRIMARY KEY, ent_no TEXT", [(1, "E1")])
    return make_sqlite(tables)


def _rel(src, dst, ck, pk, status="verified", overlap=100.0, matched=30):
    return {"source_concept": src, "target_concept": dst, "verb": "归属", "status": status,
            "evidence": {"child_key": ck, "parent_key": pk, "overlap": overlap, "matched_distinct": matched}}


def _ir():
    names = ["ent", "order_a", "bad_name", "stale_name", "cfg", "log_x", "device", "measure", "a__shell"]
    return {"objects": [{"name": n, "table": n} for n in names], "relations": [
        _rel("order_a", "ent", "ent_no", "ent_no"),
        _rel("bad_name", "ent", "ent_id", "id"),
        _rel("stale_name", "ent", "ent_no", "ent_no"),
        _rel("log_x", "cfg", "id", "id"),
        _rel("cfg", "log_x", "id", "id"),
        _rel("measure", "device", "rel_obj_no", "dev_no", status="candidate", overlap=23.4, matched=52),
    ]}


def _by(rows):
    return {(r["source"], r["target"]): r for r in rows}


class TestSkillPackage:
    def test_discoverable_runnable_and_bounded(self):
        items = {i["name"]: i for i in skill_registry.discover([os.path.join(ROOT, "skills_seed")])}
        it = items["ontology-cross-check"]
        assert it["runnable"] and it["meta"]["license"] == "Apache-2.0"
        body = skill_registry.skill_body(open(it["skill_md"], encoding="utf-8").read())
        assert len(body) <= 3500
        for rule in ("不提 id 对 id 的关系", "租户键不逐表提议", "同一对表只提一个方向", "码表判定",
                     "离群编码", "悬空但语义成立", "源库一律不改"):
            assert rule in body, rule

    def test_method_skill_maps_the_pipeline_onto_skills(self):
        """构建流程写在通用方法技能里,领域技能只引用它。"""
        items = {i["name"]: i for i in skill_registry.discover([os.path.join(ROOT, "skills_seed")])}
        body = skill_registry.skill_body(open(items["ontology-build-method"]["skill_md"], encoding="utf-8").read())
        for step in ("体检", "画像与参照", "概念层", "分轮映射", "裁决", "交叉复核与清洗", "验收"):
            assert step in body, step
        energy = skill_registry.skill_body(open(items["energy-carbon-ontology"]["skill_md"], encoding="utf-8").read())
        assert "ontology-build-method" in energy and "ontology-cross-check" in energy


class TestChannels:
    def test_name_support_and_hub_key(self, make_sqlite):
        rows, hubs = cc.check(_ir(), cc.Db(_fixture(make_sqlite)))
        r = _by(rows)[("order_a", "ent")]
        assert "ent_no" in hubs and "tenant_or_hub_key" in r["tags"]
        assert r["channels"]["name"]["state"] == "support" and r["verdict"] == "corroborated"

    def test_name_mismatch_on_surrogate_id_refutes(self, make_sqlite):
        """父键是自增 id、名称又对不上:两边编号体系不同,关系不成立。"""
        r = _by(cc.check(_ir(), cc.Db(_fixture(make_sqlite)))[0])[("bad_name", "ent")]
        assert r["verdict"] == "refuted" and r["conflicts"] == ["name"]

    def test_name_mismatch_on_business_key_is_only_stale(self, make_sqlite):
        """父键是业务编码:更可能是子表冗余存的名称过期,关系保留、送复核。"""
        r = _by(cc.check(_ir(), cc.Db(_fixture(make_sqlite)))[0])[("stale_name", "ent")]
        assert r["verdict"] == "review" and r["weak_conflicts"] == ["name"]
        assert r["channels"]["name"]["stale"] is True

    def test_id_collision_is_strong_and_bidirectional_is_weak(self, make_sqlite):
        r = _by(cc.check(_ir(), cc.Db(_fixture(make_sqlite)))[0])[("log_x", "cfg")]
        assert "id_collision" in r["conflicts"] and "bidirectional" in r["weak_conflicts"]
        assert r["verdict"] == "refuted"

    def test_apply_downgrades_only_strong_conflicts_and_keeps_history(self, make_sqlite):
        ir = _ir()
        rows, _ = cc.check(ir, cc.Db(_fixture(make_sqlite)))
        out = cc.apply(json.loads(json.dumps(ir)), rows)
        bad = out["relations"][1]
        assert bad["status"] == "candidate" and bad["evidence"]["cross_check"]["prev_status"] == "verified"
        assert out["relations"][0]["status"] == "verified"


class TestCleaning:
    def _clean(self, make_sqlite):
        db = cc.Db(_fixture(make_sqlite))
        ir = _ir()
        rows, _ = cc.check(ir, db)
        import data_clean  # cross_check 已把脚本目录加入 sys.path
        return data_clean.clean(db, db.con, cc.usable_relations(ir, rows, db), {"a__shell"})

    def test_dangling_but_coherent_relation_is_kept_with_orphans_reported(self, make_sqlite):
        rules = self._clean(make_sqlite)["rules"]
        coh = [r for r in rules if r["kind"] == "coherent_dangling_relation"]
        assert coh and coh[0]["table"] == "measure" and "rel_obj_type" in coh[0]["condition"]
        # 条件是 rel_obj_type=0:指向系统的 10 行不算悬空
        orph = next(r for r in rules if r["kind"] == "orphan_reference" and r["table"] == "measure")
        assert orph["orphan_rows"] == 160 and orph["severity"] == "high"

    def test_mislabelled_codes_are_found_through_the_parent_category(self, make_sqlite):
        rules = self._clean(make_sqlite)["rules"]
        out = next(r for r in rules if r["kind"] == "outlier_code" and r["column"] == "energy_type")
        mete = next(g for g in out["groups"] if g["category"] == "EMTR")
        assert mete["expected"] == "2" and mete["outliers"] == {"3": 3}
        assert "CASE" in out["sql"] and "device" in out["determined_by"]

    def test_codebook_from_data_beats_a_wrong_comment(self, make_sqlite):
        res = self._clean(make_sqlite)
        assert res["assigned"]["price.energy_type"]["book"] == "sys_dict_item:energy_type"
        mis = next(r for r in res["rules"] if r["kind"] == "comment_code_mismatch" and r["table"] == "price")
        assert mis["explained_by"] == "sys_dict_item:energy_type"

    def test_empty_shell_bound_object_is_flagged(self, make_sqlite):
        shell = next(r for r in self._clean(make_sqlite)["rules"] if r["kind"] == "empty_shell")
        assert shell["populated"] == ["b__shell"] and shell["bound_to_empty"] == ["a__shell"]


def test_run_sh_end_to_end_is_read_only(make_sqlite, tmp_path):
    db = _fixture(make_sqlite)
    before = os.path.getmtime(db)
    irf, rep, out = tmp_path / "ir.json", tmp_path / "rep.json", tmp_path / "out.json"
    irf.write_text(json.dumps(_ir(), ensure_ascii=False), encoding="utf-8")
    p = subprocess.run(["bash", os.path.join(SKILL, "run.sh"), "--ir", str(irf), "--db", str(db),
                        "--report", str(rep), "--clean", "--apply", str(out)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    summary = json.loads(rep.read_text(encoding="utf-8"))["summary"]
    assert summary["verdicts"]["refuted"] == 3 and summary["cleaning"]["outlier_code"] >= 1
    applied = json.loads(out.read_text(encoding="utf-8"))
    assert any(o.get("evidence", {}).get("cleaning") for o in applied["objects"] if o["name"] == "measure")
    assert os.path.getmtime(db) == before
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM measure WHERE energy_type='3'").fetchone()[0] == 3


def test_db_opens_readonly_even_with_uri_characters_in_filename(make_sqlite):
    db = make_sqlite({"t": ("id INTEGER", [(1,)])}, fname="x#y?z.db")
    h = cc.Db(db)
    try:
        assert h.tables == ["t"]
        with pytest.raises(sqlite3.OperationalError):
            h.con.execute("INSERT INTO t VALUES (2)")
    finally:
        h.con.close()
