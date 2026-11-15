# -*- coding: utf-8 -*-
"""结果图表的序列整理(DR-131):月份 × 区域 × 数值这样的长表,此前被画成一条来回折返的线。"""
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CASES = ["a_long_table_is_split_into_one_series_per_group", "the_group_column_is_found_even_when_the_plan_lists_it_as_a_value",
         "a_missing_combination_is_left_empty_not_shifted", "a_wide_table_keeps_one_series_per_numeric_column",
         "two_measures_in_a_long_table_are_named_by_group_and_measure", "too_many_groups_are_not_split"]


@pytest.mark.parametrize("case", CASES)
def test_chart_series(case):
    node = shutil.which("node")
    if not node:
        pytest.skip("未安装 node")
    result = subprocess.run([node, str(ROOT / "tests/frontend/chart-series.cjs"), case], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr or result.stdout


def test_the_page_loads_the_module_and_uses_it_for_result_charts():
    index = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    assert "/assets/modules/chart-series.js" in index
    chart = index[index.index("function dqChart"):][:1200]
    assert "chartSeries(r.data,ch)" in chart
