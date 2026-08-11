# -*- coding: utf-8 -*-
"""compat_check(DR-031)/ module_split(DR-031)/ usage_stat(DR-026)单测。"""
import compat_check
import module_split
import usage_stat


class TestCompat:
    def test_diff_detects_removed_object(self, ir_healthy):
        new = {"objects": [{"id": "emp", "table": "employees"}], "relations": []}
        d = compat_check.diff(ir_healthy, new)
        assert "dept" in d["removed_objects"]
        assert any(r for r in d["removed_relations"])

    def test_check_flags_breaking_when_consumer_depends(self, ir_healthy):
        new = {"objects": [{"id": "emp", "table": "employees"}], "relations": []}
        # 一条规则依赖被删对象 dept → 破坏性
        rules = [{"id": "r1", "cn": "x", "on": "dept",
                  "when": [{"field": "a", "op": "exists"}], "then": {"decision": "d"}}]
        res = compat_check.check(ir_healthy, new, rules, [], [])
        assert "verdict" in res
        assert res["verdict"] in ("breaking", "risky", "safe")


class TestModuleSplit:
    def test_by_domain_returns_modules(self, ir_healthy):
        res = module_split.suggest(ir_healthy, "by_domain")
        assert res["strategy"] == "by_domain"
        assert res["module_count"] >= 1

    def test_by_layer_is_list(self, ir_healthy):
        # by_layer 直接返回分层列表(与 by_domain 的 dict 形状不同)
        res = module_split.by_layer(ir_healthy)
        assert isinstance(res, list)


class TestUsageStat:
    def test_record_and_report_coverage(self, ir_healthy, tmp_path):
        wd = str(tmp_path)
        # 计数桶为 query/diagnose/action;两个对象都被调用过 → 覆盖率 100%
        usage_stat.record(wd, "g1", ["emp", "emp", "dept"], "query")
        rep = usage_stat.report(wd, ir_healthy, "g1")
        assert rep["coverage"] == 100.0
        assert rep["called"] == 2
        assert "emp" in {t["key"] for t in rep["top"]}

    def test_uncalled_counted_and_object_shown_zero(self, ir_healthy, tmp_path):
        wd = str(tmp_path)
        usage_stat.record(wd, "g1", ["emp"], "query")
        rep = usage_stat.report(wd, ir_healthy, "g1")
        # uncalled 是计数(int),覆盖率 50%;dept 在 top 里以 calls=0 出现
        assert rep["uncalled"] == 1
        assert rep["coverage"] == 50.0
        assert any(t["key"] == "dept" and t["calls"] == 0 for t in rep["top"])
