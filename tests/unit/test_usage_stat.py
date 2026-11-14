# -*- coding: utf-8 -*-
"""usage_stat(DR-026)单测。"""
import usage_stat


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
