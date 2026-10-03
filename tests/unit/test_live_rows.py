# -*- coding: utf-8 -*-
"""逻辑删除的「未删除」取值:体检、复核、问数使用同一定义(valid_rows_rules.json 的 soft_delete.detect)。

删除标记在不同库里写法不同:0/1、'N'/'Y'、NULL 表示未删除。复核按库里实际出现的取值记录,
问数的关联提示与本体约束校验据此生成、判断条件,不再固定为「= 0」。
"""
import importlib.util
import os

import build_prep
import ontology_domain as od
import server

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_spec = importlib.util.spec_from_file_location(
    "cross_check", os.path.join(ROOT, "skills_seed", "ontology-cross-check", "scripts", "cross_check.py"))
assert _spec and _spec.loader
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)


def _ev(**vr):
    return {"child_key": "type_code", "parent_key": "type_code",
            "valid_rows": {"state": "support", "rule": "soft_delete", "column": "del_flag", **vr}}


def _ir(ev):
    return {"objects": [{"name": "dev", "table": "device"}, {"name": "typ", "table": "device_type"}],
            "relations": [{"source_concept": "dev", "target_concept": "typ", "status": "verified", "evidence": ev}]}


SQL = "SELECT t.name, COUNT(*) FROM device d JOIN device_type t ON t.type_code = d.type_code"


def _ok(ev, tail):
    return server._validate_sql_ontology(SQL + tail + " GROUP BY 1", _ir(ev), strict=True)[0]


def _db(make_sqlite, rows):
    return cc.Db(make_sqlite({"device": ("dev_no TEXT, type_code TEXT", [("D1", "A"), ("D2", "B")]),
                              "device_type": ("type_code TEXT, name TEXT, del_flag TEXT", rows)}))


def test_char_flags(make_sqlite):
    db = _db(make_sqlite, [("A", "甲", "N"), ("A", "甲", "Y"), ("B", "乙", "N")])
    r = cc.valid_rows_channel(db, "device", "type_code", "device_type", "type_code",
                              {"overlap": 100.0, "parent_unique": False})
    assert r["rule"] == "soft_delete" and r["live_values"] == ["N"] and r["deleted_values"] == ["Y"]


def test_null_means_live(make_sqlite):
    db = _db(make_sqlite, [("A", "甲", None), ("A", "甲", "1"), ("B", "乙", None)])
    r = cc.valid_rows_channel(db, "device", "type_code", "device_type", "type_code",
                              {"overlap": 100.0, "parent_unique": False})
    assert r["rule"] == "soft_delete" and r["live_values"] == [None] and r["deleted_values"] == ["1"]


def test_detection_matches_the_model():
    """复核脚本独立发布,自带常量;与模型一致。"""
    det = od.MODEL["valid_rows_rules"]["soft_delete"]["detect"]
    assert cc.DEL_COL_PATTERN == det["column_pattern"]
    assert list(cc.LIVE_MARKERS) == det["live_markers"]
    assert build_prep.SOFT_DELETE.pattern == det["column_pattern"]


def test_hint_uses_char_flag():
    assert "AND device_type.del_flag = 'N'" in server._join_hints(_ir(_ev(live_values=["N"], deleted_values=["Y"])),
                                                                   ["device", "device_type"])[0]


def test_gate_accepts_equivalent_forms_for_char_flag():
    ev = _ev(live_values=["N"], deleted_values=["Y"])
    assert not _ok(ev, "")
    assert not _ok(ev, " AND t.del_flag = 0")              # 固定写 0 在这种库上等于没排除
    for tail in (" AND t.del_flag = 'N'", " AND t.del_flag IN ('N')", " AND t.del_flag <> 'Y'",
                 " AND t.del_flag != 'Y'", " AND t.del_flag NOT IN ('Y')", " AND COALESCE(t.del_flag, 'N') = 'N'"):
        assert _ok(ev, tail), tail


def test_null_live_requires_keeping_nulls():
    ev = _ev(live_values=[None, 0], deleted_values=[1])
    assert "(device_type.del_flag IS NULL OR device_type.del_flag = 0)" in \
        server._join_hints(_ir(ev), ["device", "device_type"])[0]
    assert not _ok(ev, " AND t.del_flag = 0")              # 会丢掉 NULL 表示的未删除行
    assert not _ok(ev, " AND t.del_flag <> 1")             # NULL <> 1 不成立,同样丢行
    for tail in (" AND (t.del_flag IS NULL OR t.del_flag = 0)", " AND COALESCE(t.del_flag, 0) = 0",
                 " AND IFNULL(t.del_flag, 0) <> 1", " AND (t.del_flag IS NULL OR t.del_flag <> 1)"):
        assert _ok(ev, tail), tail


def test_legacy_evidence_without_markers_means_zero():
    ev = _ev()
    assert "AND device_type.del_flag = 0" in server._join_hints(_ir(ev), ["device", "device_type"])[0]
    assert _ok(ev, " AND t.del_flag = 0") and _ok(ev, " AND t.del_flag = '0'") and not _ok(ev, "")


def test_prep_counts_char_flags_as_deleted(make_sqlite):
    db = make_sqlite({"device_type": ("type_code TEXT, del_flag TEXT", [("A", "N"), ("A", "Y"), ("B", None), ("C", "Y")])})
    prof = build_prep.profile(db)
    assert {s["table"]: s["deleted_pct"] for s in prof["soft_delete"]}["device_type"] == 50.0
