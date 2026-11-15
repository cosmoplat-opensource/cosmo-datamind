# -*- coding: utf-8 -*-
"""答案的结果记录与同源数字核对(DR-122):表格、文字说明和依据卡引用同一个结果;文字里找不到出处的数字要标出来。"""
import pytest

import answer_record as ar
import server

SALES = {"title": "月度收入", "sql": "SELECT month, revenue FROM t", "data": {"columns": ["月", "收入_万"], "rows": [
    {"月": "2026-04", "收入_万": 12000.0}, {"月": "2026-05", "收入_万": 12800.5}, {"月": "2026-06", "收入_万": 13521.8}]}}


# ── 结果编号 ────────────────────────────────────────────────────────

def test_the_same_query_on_the_same_data_gets_the_same_id():
    again = {"title": "换个标题", "sql": SALES["sql"], "data": {"columns": ["月", "收入_万"], "rows": [dict(r) for r in SALES["data"]["rows"]]}}
    assert ar.result_id(SALES) == ar.result_id(again) and ar.result_id(SALES).startswith("r_")
    changed = {**SALES, "data": {**SALES["data"], "rows": SALES["data"]["rows"][:2]}}
    assert ar.result_id(changed) != ar.result_id(SALES)
    assert ar.result_id({**SALES, "sql": "SELECT 1"}) != ar.result_id(SALES)


def test_the_record_lists_graphs_release_state_and_results():
    res = [dict(SALES)]
    rec = ar.build(res, graphs=["built_x"], release="published", source="demo")
    assert res[0]["result_id"] == rec["results"][0]["result_id"]
    assert rec["graphs"] == ["built_x"] and rec["release"] == "published" and rec["results"][0]["row_count"] == 3
    assert rec["answer_id"] == ar.build([dict(SALES)], graphs=["built_x"])["answer_id"]
    assert rec["answer_id"] != ar.build([dict(SALES)], graphs=["built_y"])["answer_id"]


# ── 数字核对 ────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "6 月收入 13521.8 万元,是三个月里最高的。",
    "6 月收入约 1.35 亿元。",                                   # 万 → 亿
    "三个月合计 38322.3 万元,月均 12774.1 万元。",              # 合计、平均
    "6 月比 5 月增加 721.3 万元,环比增长 5.6%。",               # 相邻值的差与增长率
    "6 月收入 13,521.8 万元。",                                  # 千分位
    "6 月收入约 13522 万元。",                                   # 四舍五入
    "6 月占三个月合计的 35.3%。",                                # 占比
    "共 3 个月的数据,2026-06 最高。",                           # 小整数与日期不检查
])
def test_numbers_that_come_from_the_result_pass(text):
    assert ar.check_narrative(text, [SALES])["unverified"] == []


@pytest.mark.parametrize("text,bad", [
    ("6 月收入 15321.8 万元。", ["15321.8"]),                    # 数字抄错
    ("环比增长 12.4%。", ["12.4%"]),                             # 算错的增长率
    ("全年预计达到 160000 万元。", ["160000"]),                  # 结果里没有的推算
])
def test_numbers_without_a_source_are_listed(text, bad):
    assert ar.check_narrative(text, [SALES])["unverified"] == bad


def test_numbers_from_the_question_and_from_declared_values_are_not_flagged():
    assert ar.check_narrative("订单 1001 的金额是 13521.8", [SALES], question="订单 1001 的金额")["unverified"] == []
    text = "4 行中 1 行命中,阈值 72.5 小时。"
    assert ar.check_narrative(text, [], extra=[72.5])["unverified"] == []
    assert ar.check_narrative(text, [])["unverified"] == ["72.5"]


def test_text_without_checkable_numbers_is_not_checked():
    assert ar.check_narrative("各月收入稳步上升。", [SALES]) == {"checked": 0, "unverified": []}
    assert ar.flag_text({"unverified": []}) == "" and "以表格为准" in ar.flag_text({"unverified": ["12.4%"]})


def test_identifiers_dates_and_versions_are_not_treated_as_numbers():
    nums = [t for t, *_ in ar.numbers_in("订单 P1001 于 2026-11-20 10:30 完工,版本 v2.3,编号 A-205,合计 13521.8。")]
    assert nums == ["13521.8"]


# ── 接入问数 ────────────────────────────────────────────────────────

def _chat(monkeypatch, narrative):
    monkeypatch.setattr(server, "agent_sql_plan", lambda *a, **k: None)
    monkeypatch.setattr(server, "_match_qa_skill", lambda _q: None)
    monkeypatch.setattr(server, "_rule_summary", lambda results: narrative(results))
    server._QA_CACHE.clear()
    return server.app.test_client().post("/api/chat", json={"q": "各月收入趋势", "nocache": True}).get_json()


def test_answers_carry_a_record_and_the_card_refers_to_the_same_result(monkeypatch):
    d = _chat(monkeypatch, lambda results: "各月收入见下表。")
    rec = d["answer_record"]
    assert rec["results"][0]["result_id"] == d["results"][0]["result_id"] and rec["graphs"]
    assert d["evidence_card"]["basis"][0]["result_id"] == d["results"][0]["result_id"]
    assert rec["narrative_check"]["unverified"] == [] and not any("以表格为准" in f for f in d["evidence_card"]["flags"])


def test_a_narrative_number_that_is_not_in_the_result_is_flagged_on_the_card(monkeypatch):
    d = _chat(monkeypatch, lambda results: "最近一个月收入 987654.3 万元。")
    assert d["answer_record"]["narrative_check"]["unverified"] == ["987654.3"]
    assert any("987654.3" in f and "以表格为准" in f for f in d["evidence_card"]["flags"])


def test_a_narrative_that_quotes_the_result_is_clean(monkeypatch):
    d = _chat(monkeypatch, lambda results: f"最近一个月收入 {list(results[0]['data']['rows'][-1].values())[-1]} 万元。")
    assert d["answer_record"]["narrative_check"]["unverified"] == []
