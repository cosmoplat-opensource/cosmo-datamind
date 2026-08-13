# -*- coding: utf-8 -*-
"""共享测试夹具(IR-007 TDD 底座)。

单测直接 import 根目录的确定性模块(health_check / rule_engine / …);
pythonpath 由 pyproject 的 [tool.pytest.ini_options] 注入。
夹具刻意最小:一个两对象一关系的健康 IR、一个规则集、一个可复用的临时 SQLite 建库器。
"""
import sqlite3
import pytest


@pytest.fixture
def ir_healthy():
    """最小健康本体:员工 →(belongs_to)→ 部门,verified 边。"""
    return {
        "objects": [
            {"id": "emp", "cn": "员工", "name": "employees", "table": "employees"},
            {"id": "dept", "cn": "部门", "name": "departments", "table": "departments"},
        ],
        "relations": [
            {"source_concept": "emp", "target_concept": "dept",
             "verb": "belongs_to", "status": "verified"},
        ],
    }


@pytest.fixture
def ir_with_isolated(ir_healthy):
    """在健康 IR 上加一个孤岛对象(不参与任何关系)。"""
    ir = {"objects": list(ir_healthy["objects"]), "relations": list(ir_healthy["relations"])}
    ir["objects"].append({"id": "audit_log", "cn": "审计日志", "table": "audit_log"})
    return ir


@pytest.fixture
def rules_amount():
    """一条大额订单规则 + 一条与之条件相同但结论相反的规则(用于冲突/矛盾测试)。"""
    return [
        {"id": "r_high", "cn": "大额订单需总监审批", "on": "sales_order",
         "when": [{"field": "amount", "op": ">=", "value": 100000}],
         "then": {"decision": "需总监审批", "action": "escalate", "severity": "high"},
         "note": "2026 版审批权限手册"},
    ]


@pytest.fixture
def make_sqlite(tmp_path):
    """返回一个 (tables:dict[str, (ddl, rows)]) -> db_path 的建库器。

    tables 形如 {"customers": ("customer_id INTEGER PRIMARY KEY, name TEXT",
                                 [(1,'A'),(2,'B')])}
    """
    def _build(tables, fname="fx.db"):
        db = tmp_path / fname
        con = sqlite3.connect(str(db))
        for tname, (ddl, rows) in tables.items():
            con.execute(f"CREATE TABLE {tname} ({ddl})")
            if rows:
                ph = ",".join("?" * len(rows[0]))
                con.executemany(f"INSERT INTO {tname} VALUES ({ph})", rows)
        con.commit()
        con.close()
        return str(db)
    return _build
