"""库内数据字典(DR-077):没有只读抽样留下的 _dm_tables/_dm_columns 时,
表与字段注释从库内的 dim_table_metadata / dim_column_metadata 读取,构建体检与构建取证都能用上。"""
from __future__ import annotations

import build_prep
import code_contracts


def _db(make_sqlite):
    return make_sqlite({
        "dim_production_line": ("line_id INTEGER PRIMARY KEY, line_name TEXT", [(i, f"L{i}") for i in range(1, 6)]),
        "fact_po_line": ("line_id INTEGER PRIMARY KEY, qty REAL", [(i, 1.0) for i in range(1, 40)]),
        "dim_equipment": ("equipment_id INTEGER PRIMARY KEY, line_id INTEGER", [(i, (i % 5) + 1) for i in range(1, 21)]),
        "dim_table_metadata": ("table_id INTEGER, table_name TEXT, table_name_cn TEXT, table_desc TEXT, table_category TEXT, table_level TEXT",
                               [(1, "dim_equipment", "设备", "生产设备台账", "维度表", "core")]),
        "dim_column_metadata": ("column_id INTEGER, table_name TEXT, column_name TEXT, column_name_cn TEXT, column_desc TEXT, ordinal INTEGER",
                                [(1, "dim_equipment", "line_id", "所属产线ID", "外键，关联dim_production_line", 2),
                                 (2, "dim_equipment", "equipment_id", "设备ID", "主键", 1)]),
    })


def test_prep_reads_in_db_dictionary(make_sqlite):
    con = build_prep._connect(_db(make_sqlite))
    tabs, cols, _rows = build_prep._comments(con)
    assert tabs["dim_equipment"].startswith("设备")
    assert "外键，关联dim_production_line" in cols[("dim_equipment", "line_id")]


def test_comment_contract_from_in_db_dictionary(make_sqlite):
    con = build_prep._connect(_db(make_sqlite))
    _tabs, cols, _rows = build_prep._comments(con)
    tab_cols = {t: [(r[1], r[2]) for r in con.execute(f'PRAGMA table_info("{t}")')]
                for t in ("dim_production_line", "fact_po_line", "dim_equipment")}
    found = code_contracts.comment_contracts(cols, tab_cols)
    assert [(c["a"], c["b"][0]) for c in found] == [(["dim_equipment", "line_id"], "dim_production_line")]


def test_reference_comment_with_chinese_name_before_table():
    tab_cols = {"t": [("a", "INTEGER")], "dim_production_line": [("line_id", "INTEGER")]}
    found = code_contracts.comment_contracts({("t", "a"): "产线ID;引用产线(dim_production_line.line_id)"}, tab_cols)
    assert found and found[0]["b"] == ["dim_production_line", "line_id"]
