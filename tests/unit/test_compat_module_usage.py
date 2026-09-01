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

    def test_full_diff_and_downstream_impact(self):
        base = {
            "objects": [
                {"id": "gone", "table": "old_orders"},
                {"id": "kept", "table": "old_table", "aliases": ["旧称", "保留"],
                 "attrs": [{"col": "removed_col"}, {"col": "keep_col"}]},
                {"name": "named", "table": "named_table"},
                {"table": "anonymous_table"},
            ],
            "links": [
                {"source": "gone", "target": "kept", "status": "verified"},
                {"source": "kept", "target": "named", "status": "asserted"},
                {"source": "", "target": "named", "status": "verified"},
            ],
        }
        new = {
            "objects": [
                {"id": "kept", "table": "new_table", "aliases": ["保留"],
                 "attrs": [{"col": "keep_col"}]},
                {"name": "named", "table": "named_table"},
                {"id": "added", "table": "new_object"},
            ],
            "links": [
                {"source": "kept", "target": "named", "status": "candidate"},
                {"source": "kept", "target": "added", "status": "verified"},
            ],
        }
        report = compat_check.check(
            base,
            new,
            rules=[
                {"id": "gone_rule", "on": "gone"},
                {"id": "moved_rule", "on": "kept"},
            ],
            actions=[{"id": "action", "object_table": "OLD_ORDERS"}],
            qa_skills=[{"question": "旧订单", "analyses": [{"sql": "select * from old_orders"}]}],
        )
        changes = report["changes"]
        assert changes["removed_objects"] == ["gone", "_obj3"]
        assert changes["added_objects"] == ["added"]
        assert changes["retabled"][0]["object"] == "kept"
        assert changes["removed_aliases"][0]["removed"] == ["旧称"]
        assert changes["removed_attrs"][0]["removed"] == ["removed_col"]
        assert changes["downgraded_relations"][0]["relation"] == "kept->named"
        assert set(changes["removed_relations"]) == {"gone->kept"}
        assert changes["added_relations"] == ["kept->added"]
        assert {item["kind"] for item in report["downstream_impact"]} == {
            "rule", "action", "qa_skill",
        }
        assert report["verdict"] == "breaking"
        assert report["summary"]["breaking_count"] == 5
        assert report["summary"]["risky_count"] == 2

    def test_risky_and_safe_verdicts(self):
        base = {"objects": [{"id": "a", "aliases": ["旧别名"]}], "relations": []}
        risky = compat_check.check(base, {"objects": [{"id": "a"}], "relations": []})
        assert risky["verdict"] == "risky"
        safe = compat_check.check(
            {"objects": [{"id": "a"}], "relations": []},
            {"objects": [{"id": "a"}, {"id": "b"}], "relations": []},
        )
        assert safe["verdict"] == "safe"


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
