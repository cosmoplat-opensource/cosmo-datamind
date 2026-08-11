# -*- coding: utf-8 -*-
"""quick_build 单测 —— IR-007 的红→绿示范。

先红:quick_build.py 在 import 时解析 sys.argv 并连库(第 9-14 行、103-152 行都在模块顶层),
      纯 import 直接 IndexError,任何单测都跑不起来。
后绿:把 CLI/构建逻辑收进 build()+`if __name__=="__main__"` 守卫后,
      纯函数(key_stem/key_name_ok)可测,build() 也可编程调用(server 子进程行为不变)。
"""
import json
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

    def test_build_self_referential_fk_currently_missed(self, make_sqlite, tmp_path):
        # 现状锁定:employees.reports_to → employees.employee_id 是自引用键,
        # 当前枚举跳过 pt==t,必然发现不了。DR-036 落地后此测试应反转为「能发现」。
        db = make_sqlite({
            "employees": ("employee_id INTEGER PRIMARY KEY, name TEXT, reports_to INTEGER",
                          [(1, "CEO", None), (2, "VP", 1), (3, "IC", 2)]),
        })
        out = str(tmp_path / "ir.json")
        quick_build.build(db, out, "fx")
        ir = json.loads(open(out, encoding="utf-8").read())
        self_rels = [r for r in ir["relations"]
                     if r["source_concept"] == "employees" and r["target_concept"] == "employees"]
        assert self_rels == [], "现状:自引用键发现不了(DR-036 将改进后反转此断言)"
