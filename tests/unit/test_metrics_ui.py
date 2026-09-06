# -*- coding: utf-8 -*-
"""指标中心与口径卡的界面契约(DR-054):状态可见、确认口径按钮的边界、图谱可切换。

与 test_ui.py 的浏览器走查互补:此处只做静态断言,离线秒级,防止改版时悄悄丢掉
「状态标注」「已核验不可由人授予」这类不可退让的呈现约束。
"""
import pathlib
import re

UI = (pathlib.Path(__file__).resolve().parents[2] / "ui" / "index.html").read_text(encoding="utf-8")


def test_status_vocabulary_covers_all_states_and_labels_are_chinese():
    m = re.search(r"const MST=\{(.+?)\};", UI, re.S)
    assert m, "指标状态词表缺失"
    body = m.group(1)
    for key, label in (("certified", "业务已确认"), ("verified", "已核验"), ("candidate", "待核验"),
                       ("deprecated", "已停用"), ("heuristic", "旧口径(启发式)")):
        assert key in body and label in body


def test_metrics_table_shows_status_and_caliber_columns():
    assert "<th>状态</th><th>口径</th>" in UI
    assert "mstTag(mStatus(x))" in UI and "mCaliber(x)" in UI


def test_metric_page_has_graph_selector_and_readjudicate():
    assert 'id="m_sel"' in UI and "graphOptions(M_GRAPHS" in UI
    assert "metricReadjudicate" in UI and "/api/metric/adjudicate" in UI
    assert "'/api/metrics?graph='" in UI, "指标列表必须按所选图谱取数"


def test_ui_never_offers_verified_as_a_human_action():
    """人只能授予 certified/deprecated 或撤回;界面不得出现把指标置为 verified 的入口。"""
    assert "metricStatus(" in UI
    assert not re.search(r"metricStatus\([^)]*'verified'", UI)
    for status in ("'certified'", "'deprecated'", "'candidate'"):
        assert f"metricStatus(${{jsAttr(name)}},{status})" in UI
    assert "「已核验」只能由核验产生" in UI


def test_certified_requires_reviewer_before_request():
    assert "确认口径须填写确认人" in UI
    assert "if(status==='certified'&&!who)" in UI


def test_legacy_metric_cannot_be_certified_in_ui():
    assert "不能确认为业务口径" in UI


def test_metric_cards_expose_status_and_warn_on_unverified():
    assert "m.status?mstTag(m.status)" in UI
    assert "该口径尚未核验,数值仅供参考" in UI
    assert "计算口径:<code" in UI


def test_lineage_shows_evidence_and_dimension_reachability():
    for phrase in ("核验证据", "与参照一致", "与参照不一致", "无参照", "可下钻维度", "扇出风险", "编译 SQL"):
        assert phrase in UI, phrase
