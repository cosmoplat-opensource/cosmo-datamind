# -*- coding: utf-8 -*-
"""多级表头(DR-135):Excel 里合并单元格的两三行表头导出成 CSV 后,上层只在第一格有值,其余为空。

此前只认第一行,列名重复时整份文件上传失败;年份写在上层表头时,按年份查数也无从下手。"""
import header_parse as H

WIDE = [["地区", "2025年", "", "2026年", ""],
        ["", "收入", "成本", "收入", "成本"],
        ["华东", "10", "6", "12", "7"],
        ["华北", "8", "5", "9", "5.5"]]


def test_header_rows_are_detected_and_combined():
    h = H.parse(WIDE)
    assert h["header_rows"] == 2
    assert h["columns"] == ["地区", "2025年_收入", "2025年_成本", "2026年_收入", "2026年_成本"]
    assert h["data"] == WIDE[2:]
    assert h["parts"][1] == ["2025年", "收入"] and h["parts"][0] == ["地区"]


def test_a_single_header_row_is_left_alone():
    rows = [["月份", "收入", "成本"], ["2026-01", "10", "6"], ["2026-02", "12", "7"]]
    h = H.parse(rows)
    assert h["header_rows"] == 1 and h["columns"] == ["月份", "收入", "成本"] and h["long"] is None


def test_year_like_numbers_in_the_upper_row_still_count_as_header():
    rows = [["产线", "2025", "", "2026", ""], ["", "产量", "良率", "产量", "良率"], ["一线", "100", "0.95", "120", "0.96"]]
    h = H.parse(rows)
    assert h["header_rows"] == 2 and h["columns"][1] == "2025_产量"


def test_period_columns_are_also_given_in_long_form():
    h = H.parse(WIDE)
    long = h["long"]
    assert long["columns"] == ["地区", "期间", "项目", "值"]
    assert long["rows"][:4] == [["华东", "2025年", "收入", "10"], ["华东", "2025年", "成本", "6"],
                                ["华东", "2026年", "收入", "12"], ["华东", "2026年", "成本", "7"]]
    assert len(long["rows"]) == 8


def test_no_long_form_when_the_upper_level_is_not_a_period():
    rows = [["地区", "销售", "", "采购", ""], ["", "金额", "笔数", "金额", "笔数"], ["华东", "10", "2", "6", "1"]]
    h = H.parse(rows)
    assert h["header_rows"] == 2 and h["columns"][1] == "销售_金额" and h["long"] is None


def test_three_header_rows():
    rows = [["地区", "2026年", "", "", ""], ["", "一季度", "", "二季度", ""], ["", "收入", "成本", "收入", "成本"],
            ["华东", "3", "2", "4", "2"]]
    h = H.parse(rows)
    assert h["header_rows"] == 3 and h["columns"][3] == "2026年_二季度_收入"
    assert h["long"]["columns"] == ["地区", "期间", "项目", "值"] and h["long"]["rows"][2] == ["华东", "2026年_二季度", "收入", "4"]


def test_a_data_table_whose_second_row_is_text_is_not_mistaken_for_a_header():
    rows = [["客户", "地区", "等级"], ["甲公司", "华东", "A"], ["乙公司", "华北", "B"]]
    assert H.parse(rows)["header_rows"] == 1
