# -*- coding: utf-8 -*-
"""quick_build 单测 —— IR-007 的红→绿示范。

先红:quick_build.py 在 import 时解析 sys.argv 并连库(第 9-14 行、103-152 行都在模块顶层),
      纯 import 直接 IndexError,任何单测都跑不起来。
后绿:把 CLI/构建逻辑收进 build()+`if __name__=="__main__"` 守卫后,
      纯函数(key_stem/key_name_ok)可测,build() 也可编程调用(server 子进程行为不变)。
"""
import json
import sqlite3
import quick_build  # 先红:此行在守卫加入前即抛 IndexError


class TestPureHelpers:
    def test_key_stem_strips_id_suffix(self):
        assert quick_build.key_stem("customer_id") == "customer"
        assert quick_build.key_stem("OrderCode") == "order"
        assert quick_build.key_stem("id") == ""

    def test_key_name_ok_same_stem(self):
        # 词根相同/缩写相容 → 放行
        assert quick_build.key_name_ok("customer_id", "CustomerId") is True
        assert quick_build.key_name_ok("prod_id", "product_id") is True

    def test_key_name_ok_rejects_mismatch(self):
        # 已知盲区:自引用/角色键词根不一致 → 当前被拒(DR-036 将改进,此处锁现状)
        assert quick_build.key_name_ok("ReportsTo", "EmployeeId") is False
        assert quick_build.key_name_ok("order_id", "customer_id") is False


class TestBuildIntegration:
    def test_build_ignores_sqlite_internal_tables(self, make_sqlite, tmp_path):
        db = make_sqlite({"business_table": ("id INTEGER PRIMARY KEY", [(1,)])})
        with sqlite3.connect(db) as con:
            con.execute("ANALYZE")
        out = str(tmp_path / "ir.json")
        ir = quick_build.build(db, out, "fx")
        assert [obj["name"] for obj in ir["objects"]] == ["business_table"]
        assert ir["scenario"]["object_count"] == 1

    def test_declared_fk_suppresses_inferred_reverse_edge(self, make_sqlite, tmp_path):
        db = make_sqlite({
            "parents": ("parent_id INTEGER PRIMARY KEY", [(1,), (2,), (3,)]),
            "children": (
                "child_id INTEGER PRIMARY KEY, parent_id INTEGER, "
                "FOREIGN KEY(parent_id) REFERENCES parents(parent_id)",
                [(10, 1), (11, 2), (12, 3)],
            ),
        })
        out = str(tmp_path / "ir.json")
        ir = quick_build.build(db, out, "fx")
        pairs = {(r["source_concept"], r["target_concept"]) for r in ir["relations"]}
        assert ("children", "parents") in pairs
        assert ("parents", "children") not in pairs

    def test_build_emits_verified_overlap_relation(self, make_sqlite, tmp_path):
        # orders.customer_id 100% 落在 customers.customer_id,且父键唯一、命名相容 → verified
        db = make_sqlite({
            "customers": ("customer_id INTEGER PRIMARY KEY, name TEXT",
                          [(1, "A"), (2, "B"), (3, "C")]),
            "orders": ("order_id INTEGER PRIMARY KEY, customer_id INTEGER",
                       [(10, 1), (11, 2), (12, 1), (13, 3)]),
        })
        out = str(tmp_path / "ir.json")
        quick_build.build(db, out, "fx")
        ir = json.loads(open(out, encoding="utf-8").read())
        rels = ir["relations"]
        verified = [r for r in rels if r["status"] == "verified"
                    and r["source_concept"] == "orders" and r["target_concept"] == "customers"]
        assert verified, f"应发现 orders→customers 的 verified 关系,实得: {rels}"
        assert verified[0]["overlap"] == 100.0

    def test_build_self_referential_fk_discovered(self, make_sqlite, tmp_path):
        # DR-036:employees.reports_to → employees.employee_id 自引用键须被发现。
        # reports_to distinct {1,2} 全落 employee_id {1,2,3} → 100% 重叠·父键唯一·角色 self 命名有据。
        db = make_sqlite({
            "employees": ("employee_id INTEGER PRIMARY KEY, name TEXT, reports_to INTEGER",
                          [(1, "CEO", None), (2, "VP", 1), (3, "IC", 2), (4, "IC2", 2)]),
        })
        out = str(tmp_path / "ir.json")
        quick_build.build(db, out, "fx")
        ir = json.loads(open(out, encoding="utf-8").read())
        self_rels = [r for r in ir["relations"]
                     if r["source_concept"] == "employees" and r["target_concept"] == "employees"]
        assert self_rels, "DR-036:自引用键应被发现"
        assert self_rels[0]["status"] == "verified"
        assert self_rels[0].get("self_ref") is True   # 标记为有意自引用(供 health_check 豁免自反误判)

    def test_direction_dim_pk_matched_by_fact_is_fact_to_dim(self, make_sqlite, tmp_path):
        # DR-037 接线:dim 的 PK(bu_id 唯一)与 fact 的非唯一列(bu_id 有重复)重叠。
        # 真方向是 fact→dim(多→一),不是 dim→fact。抑制反向边,让正向自然发现。
        # fact_budget 无声明 PK(常见于事实表),使 parent_key 落到 bu_id 列,复现反向边。
        db = make_sqlite({
            "dim_bu": ("bu_id INTEGER PRIMARY KEY, name TEXT", [(1, "A"), (2, "B"), (3, "C")]),
            "fact_budget": ("bu_id INTEGER, amt REAL",
                            [(1, 5.0), (1, 6.0), (2, 7.0), (3, 8.0)]),
        })
        out = str(tmp_path / "ir.json")
        quick_build.build(db, out, "fx")
        ir = json.loads(open(out, encoding="utf-8").read())
        pairs = {(r["source_concept"], r["target_concept"]) for r in ir["relations"]}
        assert ("fact_budget", "dim_bu") in pairs, f"应得正向 fact→dim,实得: {pairs}"
        assert ("dim_bu", "fact_budget") not in pairs, "反向 dim→fact 边应被抑制"

    def test_camelcase_role_key_discovered(self, make_sqlite, tmp_path):
        # 无下划线的驼峰角色键 ManagerId 也须识别(归一到 snake)
        db = make_sqlite({
            "staff": ("StaffId INTEGER PRIMARY KEY, name TEXT, ManagerId INTEGER",
                      [(1, "A", None), (2, "B", 1), (3, "C", 1), (4, "D", 2)]),
        })
        out = str(tmp_path / "ir.json")
        quick_build.build(db, out, "fx")
        ir = json.loads(open(out, encoding="utf-8").read())
        self_rels = [r for r in ir["relations"]
                     if r["source_concept"] == "staff" and r["target_concept"] == "staff"]
        assert self_rels, "DR-036:驼峰角色键 ManagerId 应被发现为自引用"

    def test_repeated_builds_do_not_reuse_uniqueness_cache(self, make_sqlite, tmp_path):
        first = make_sqlite({
            "customers": ("customer_id INTEGER", [(1,), (2,)]),
            "orders": ("customer_id INTEGER", [(1,), (2,)]),
        }, fname="first.db")
        second = make_sqlite({
            "customers": ("customer_id INTEGER", [(1,), (1,)]),
            "orders": ("customer_id INTEGER", [(1,), (1,)]),
        }, fname="second.db")
        first_out = str(tmp_path / "first.json")
        second_out = str(tmp_path / "second.json")
        quick_build.build(first, first_out, "first")
        quick_build.build(second, second_out, "second")
        second_ir = json.loads(open(second_out, encoding="utf-8").read())
        assert not any(
            relation["status"] == "verified"
            and relation["source_concept"] == "orders"
            and relation["target_concept"] == "customers"
            for relation in second_ir["relations"]
        )
