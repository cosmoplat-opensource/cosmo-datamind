# -*- coding: utf-8 -*-
"""指标中心与计算规则卡的界面契约(DR-054):状态可见、业务确认按钮的边界、图谱可切换。

与 test_ui.py 的浏览器走查互补:此处只做静态断言,离线秒级,防止改版时悄悄丢掉
「状态标注」「已核验不可由人授予」这类不可退让的呈现约束。
"""
import pathlib
import re

UI_DIR = pathlib.Path(__file__).resolve().parents[2] / "ui"
UI = (UI_DIR / "index.html").read_text(encoding="utf-8")
METRICS = (UI_DIR / "modules" / "metrics.js").read_text(encoding="utf-8")


def test_metric_logic_is_loaded_from_its_own_script():
    assert re.search(r'<script src="/assets/modules/metrics\.js\?v=\d+"></script>', UI)
    assert "async function metrics()" not in UI and "M_METRICS_REQUEST" not in UI


def test_status_vocabulary_covers_all_states_and_labels_are_chinese():
    m = re.search(r"const MST=\{(.+?)\};", METRICS, re.S)
    assert m, "指标状态词表缺失"
    body = m.group(1)
    for key, label in (("certified", "业务已确认"), ("verified", "已核验"), ("candidate", "待核验"),
                       ("deprecated", "已停用"), ("heuristic", "旧规则(启发式)")):
        assert key in body and label in body


def test_metrics_table_shows_status_and_caliber_columns():
    assert "<th>状态</th><th>计算规则</th>" in METRICS
    assert "mstTag(mStatus(x))" in METRICS and "mCaliber(x)" in METRICS


def test_metric_page_has_graph_selector_and_readjudicate():
    assert 'id="m_sel"' in UI and "graphOptions(M_GRAPHS" in METRICS
    assert "metricReadjudicate" in UI and "/api/metric/adjudicate" in METRICS
    assert "'/api/metrics?graph='" in METRICS, "指标列表必须按所选图谱取数"


def test_ui_never_offers_verified_as_a_human_action():
    """人只能授予 certified/deprecated 或撤回;界面不得出现把指标置为 verified 的入口。"""
    assert "metricStatus(" in METRICS
    assert not any(re.search(r"metricStatus\([^)]*'verified'", source) for source in (UI, METRICS))
    for status in ("'certified'", "'deprecated'", "'candidate'"):
        assert f"metricStatus(${{jsAttr(name)}},{status})" in METRICS
    assert "「已核验」只能由核验产生" in METRICS


def test_certified_requires_reviewer_before_request():
    assert "业务确认须填写确认人" in METRICS
    assert "if(status==='certified'&&!who)" in METRICS


def test_legacy_metric_cannot_be_certified_in_ui():
    assert "不能做业务确认" in METRICS


def test_metric_cards_expose_status_and_warn_on_unverified():
    assert "m.status?mstTag(m.status)" in UI
    assert "该计算规则尚未核验，数值仅供参考" in UI
    assert "计算规则:<code" in UI


def test_lineage_shows_evidence_and_dimension_reachability():
    for phrase in ("核验证据", "与参照一致", "与参照不一致", "无参照", "可下钻维度", "扇出风险", "编译 SQL"):
        assert phrase in METRICS, phrase
