# -*- coding: utf-8 -*-
"""深度问数的本体约束说明必须区分规划候选与 SQL 执行事实。"""
import re
from pathlib import Path

import server


ROOT = Path(__file__).resolve().parents[2]
UI = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
MODULE = (ROOT / "ui" / "modules" / "qa-anchor.js").read_text(encoding="utf-8")
STYLE = (ROOT / "ui" / "styles" / "qa-anchor.css").read_text(encoding="utf-8")


def _anchor():
    return {
        "ontology": {"objects": 108, "relations": 20, "names": ["示例企业数据本体"]},
        "objects": [
            {"table": "fact_sales", "cn": "销售事实", "reason": "关键词命中", "hits": ["销售"]},
            {"table": "dim_region", "cn": "区域", "reason": "沿本体关系召回"},
            {"table": "dim_product", "cn": "产品", "reason": "沿本体关系召回"},
        ],
        "relations": [
            {"s": "fact_sales", "t": "dim_region", "key": "fact_sales.region_id = dim_region.region_id"},
            {"s": "fact_sales", "t": "dim_product", "key": "fact_sales.product_id = dim_product.product_id"},
        ],
    }


def test_backend_explanation_uses_successful_sql_not_relation_candidates():
    anchor = _anchor()
    explanation = server._qa_anchor_explanation(anchor, [{
        "sql": "SELECT month, SUM(amount) FROM fact_sales GROUP BY month"}])
    assert explanation["phase"] == "complete"
    assert explanation["used_tables"] == ["fact_sales"]
    assert explanation["sql_join_count"] == 0
    assert explanation["relation_candidate_count"] == 2
    assert explanation["context_only_tables"] == ["dim_region", "dim_product"]


def test_backend_explanation_counts_real_join_keywords_and_deduplicates_tables():
    anchor = _anchor()
    explanation = server._qa_anchor_explanation(anchor, [{
        "sql": "SELECT * FROM fact_sales f JOIN dim_region r ON f.region_id=r.region_id"}, {
        "sql": "SELECT COUNT(*) FROM fact_sales"}])
    assert explanation["used_tables"] == ["fact_sales", "dim_region"]
    assert explanation["sql_join_count"] == 1
    assert explanation["result_sql_count"] == 2


def test_ui_module_load_order_and_plain_language_information_architecture():
    assert '/assets/styles/qa-anchor.css?v=1' in UI
    assert '/assets/modules/qa-anchor.js?v=4' in UI
    assert UI.index('/assets/modules/qa-anchor.js?v=4') < UI.index('<script>const $=')
    for text in ("SQL 最终直接使用", "为什么选入规划上下文", "候选不等于实际使用",
                 "本次 SQL 没有执行 JOIN", "查看关系图（技术证据）"):
        assert text in MODULE
    assert "function dqAnchorWithResults" in MODULE
    assert "d.anchor=dqAnchorWithResults(d.anchor,d.results)" in UI
    assert ".qa-proof-flow" in STYLE and ".qa-proof-grid" in STYLE


def test_index_keeps_anchor_module_out_of_monolith():
    assert "function dqAnchorHTML" not in UI
    assert len(UI.splitlines()) < 2500


def test_relation_diagram_stays_readable_and_routes_around_boxes():
    """关系图(技术证据)按原尺寸显示、不随窄面板缩小(缩到三成时字只有 4 px),字号不小于 11 px;
    直线会穿过无关对象框时改走弧线,不让读者误以为连到了中间那个框。"""
    fn = MODULE[MODULE.index("function dqAnchorGraph"):MODULE.index("function dqAnchorHTML")]
    assert "max-width:none" in fn and "style=\"width:100%" not in fn
    sizes = [float(x) for x in re.findall(r'font-size="([\d.]+)"', fn)]
    assert sizes and min(sizes) >= 11
    assert "function dqRoute" in MODULE and "dqRoute(" in fn
