# -*- coding: utf-8 -*-
"""问数评测的统一比对规则、重复与配对区间、候选上限(DR-134)。

对比实验里,两边用了不同的结果比对规则(集合 / 多重集),多带一列也判错,
「领先 5–8 点」大半来自评分规则而不是方法本身。评测改为:各组用同一套比对规则,
同时报告严格一致与列容忍两种口径;可重复多次,组间差给出按题配对的区间;
另外统计参考答案所需的表和列是否进入了给模型的上下文(候选上限)。"""
import pytest

import qa_eval_stats as E


def test_result_sets_compare_as_sets_with_numeric_rounding():
    gold = (["n"], [(70477.8,)])
    assert E.compare(gold, (["s"], [(70477.80000001,)]))["strict"] is True
    assert E.compare(gold, (["s"], [(70477.8,), (70477.8,)]))["strict"] is True        # 集合比较:重复行不计
    assert E.compare(gold, (["s"], [(704778000.0,)]))["strict"] is False
    assert E.compare((["a", "b"], [(1, "x"), (2, "y")]), (["b", "a"], [("y", 2), ("x", 1)]))["strict"] is False   # 列序不同即不同
    assert E.compare((["a"], []), (["a"], []))["strict"] is True


def test_the_column_tolerant_reading_allows_extra_and_reordered_columns():
    gold = (["月份", "毛利率"], [("2026-04", 14.97), ("2026-05", 14.71)])
    pred = (["month", "amount", "rate"], [("2026-04", 100.0, 14.97), ("2026-05", 90.0, 14.71)])
    out = E.compare(gold, pred)
    assert out == {"strict": False, "columns": True}
    pred_wrong = (["month", "rate"], [("2026-04", 14.97), ("2026-05", 99.0)])
    assert E.compare(gold, pred_wrong)["columns"] is False
    assert E.compare(gold, (["month"], [("2026-04",), ("2026-05",)]))["columns"] is False   # 少了列不算


def test_paired_interval_is_over_questions_and_reproducible():
    a = [1, 1, 1, 0, 1, 1, 0, 1, 1, 1]
    b = [0, 1, 0, 0, 1, 0, 0, 1, 0, 1]
    out = E.paired_ci(a, b)
    assert out["n"] == 10 and out["diff"] == pytest.approx(0.4)
    assert out["lo"] < 0.4 < out["hi"] and out == E.paired_ci(a, b)        # 固定种子,结果可复现
    same = E.paired_ci([1, 0, 1], [1, 0, 1])
    assert (same["diff"], same["lo"], same["hi"]) == (0.0, 0.0, 0.0)
    assert E.paired_ci([], [])["n"] == 0


def test_repeats_are_averaged_per_question_before_pairing():
    per_question = E.question_means({"q1": [True, False, True], "q2": [False, False, False]})
    assert per_question == {"q1": pytest.approx(2 / 3), "q2": 0.0}


def test_the_ceiling_checks_the_tables_and_columns_the_reference_needs():
    ctx = "表 fact_sales_order(订单): order_date(下单日期), amount(金额)\n库中其他表(未展开列):dim_customer"
    assert E.in_context(ctx, ["fact_sales_order"], ["amount", "order_date"]) is True
    assert E.in_context(ctx, ["fact_sales_order"], ["cost"]) is False
    assert E.in_context(ctx, ["dim_customer"], ["cust_name"]) is False          # 只列出表名、没给列,不算
    assert E.in_context(ctx, ["fact_sales_order_v2"], []) is False              # 按整词,不按子串


def test_reference_columns_are_read_from_the_reference_sql():
    cols = {"fact_sales_order": ["order_id", "order_date", "amount", "cost"]}
    assert E.reference_columns("SELECT round(sum(amount)/10000,1) FROM fact_sales_order WHERE substr(order_date,1,7)='2025-03'",
                               cols) == ["amount", "order_date"]


def test_summary_reports_rates_ceiling_and_selection_given_ceiling():
    rows = [{"arms": {"C": {"runs": [{"correct": True}], "ceiling": True}}},
            {"arms": {"C": {"runs": [{"correct": False}], "ceiling": True}}},
            {"arms": {"C": {"runs": [{"correct": False}], "ceiling": False}}}]
    s = E.arm_summary(rows, "C")
    assert s["accuracy"] == pytest.approx(1 / 3) and s["ceiling"] == pytest.approx(2 / 3)
    assert s["accuracy_given_ceiling"] == pytest.approx(1 / 2)


# ── 接入评测任务 ─────────────────────────────────────────────────────────────

def test_an_evaluation_run_repeats_compares_by_one_rule_and_reports_intervals_and_ceiling(monkeypatch, tmp_path):
    import server
    items = [{"id": "Q1", "q": "订单总金额", "gold": 216604.0, "tol": 0.01, "unit": "万元", "gold_tables": ["fact_sales_order"],
              "gold_sql": "SELECT round(sum(amount)/10000,1) FROM fact_sales_order"},
             {"id": "Q2", "q": "订单数", "gold": 4731, "tol": 0, "gold_tables": ["fact_sales_order"],
              "gold_sql": "SELECT count(*) FROM fact_sales_order"}]
    monkeypatch.setattr(server, "_eval_items", lambda: items)
    monkeypatch.setattr(server, "_eval_result_file", lambda: str(tmp_path / "eval.json"))
    monkeypatch.setattr(server, "_ctx_naive", lambda: "fact_sales_order(order_id, cust_id)")
    monkeypatch.setattr(server, "_ctx_graph", lambda q: "表 fact_sales_order: amount(金额), order_id")
    monkeypatch.setattr(server, "build_context", lambda q: "表 fact_sales_order: amount(金额), order_id")
    calls = {"n": 0}
    good = {"订单总金额": "SELECT round(sum(amount)/10000,1) AS wan, 1 AS extra FROM fact_sales_order",
            "订单数": "SELECT count(*) FROM fact_sales_order"}

    def gen(q, ctx, model=None):
        calls["n"] += 1
        if "amount" not in ctx:                                    # A 组上下文里没有 amount 列,写不出来
            return "SELECT count(*) FROM fact_sales_order", ""
        return good[q], ""
    monkeypatch.setattr(server, "_eval_sql_gen", gen)
    server._run_eval(repeats=2)
    out = server.json.load(open(tmp_path / "eval.json"))
    assert calls["n"] == 2 * 3 * 2 and out["repeats"] == 2 and out["n"] == 2
    q1 = out["items"][0]["arms"]
    assert [r["correct"] for r in q1["C"]["runs"]] == [True, True]
    assert q1["C"]["runs"][0]["strict"] is False and q1["C"]["runs"][0]["columns"] is True   # 多带一列:严格口径不算,列容忍算
    assert q1["A"]["ceiling"] is False and q1["C"]["ceiling"] is True
    s = out["summary"]
    assert s["arms"]["C"]["accuracy"] == 1.0 and s["arms"]["A"]["accuracy"] == 0.5
    assert s["arms"]["A"]["ceiling"] == 0.5 and s["arms"]["C"]["accuracy_given_ceiling"] == 1.0
    assert s["arms"]["C"]["strict"] == 0.5 and s["arms"]["C"]["columns"] == 1.0
    pair = s["pairs"]["C-A"]
    assert pair["n"] == 2 and pair["diff"] == 0.5 and pair["lo"] <= 0.5 <= pair["hi"]
    assert out["arms"]["C"]["correct"] == 2                         # 旧字段保留:按每题平均后取整计数,界面照旧可读


def test_the_run_endpoint_accepts_a_bounded_number_of_repeats(monkeypatch):
    import server
    started = {}
    monkeypatch.setattr(server, "_eval_items", lambda: [{"id": "q", "q": "x", "gold": 1}])
    monkeypatch.setattr(server, "_run_eval_thread", lambda **kw: started.update(kw))
    server.EVAL_JOB["running"] = False
    r = server.app.test_client().post("/api/eval/run", json={"repeats": 9})
    assert r.status_code == 400
    r = server.app.test_client().post("/api/eval/run", json={"repeats": 3})
    assert r.status_code == 202 and r.get_json()["total"] == 1 * 3 * 3


def test_the_reference_values_in_the_question_set_match_the_bundled_database():
    """DR-134:演示库重建(DR-076)后题集里的参考数值没有重算,8 题里 7 题的答案是旧库的,评测一直在按错误答案判分。"""
    import json
    import sqlite3
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    items = json.load(open(root / "benchmark/qa_set.json"))["items"]
    con = sqlite3.connect(f"file:{root / 'examples/demo_metrics.db'}?mode=ro", uri=True)
    for it in items:
        got = con.execute(it["gold_sql"]).fetchone()[0]
        assert got == it.get("gold", it.get("gold_str")), (it["id"], got)


def test_the_run_judges_against_the_value_the_reference_sql_returns(monkeypatch, tmp_path):
    import server
    items = [{"id": "Q", "q": "订单数", "gold": 1, "tol": 0, "gold_tables": ["fact_sales_order"],
              "gold_sql": "SELECT count(*) FROM fact_sales_order"}]            # 题集里写错的参考值:以参考 SQL 的结果为准
    monkeypatch.setattr(server, "_eval_items", lambda: items)
    monkeypatch.setattr(server, "_eval_result_file", lambda: str(tmp_path / "e.json"))
    monkeypatch.setattr(server, "_eval_sql_gen", lambda q, ctx, model=None: ("SELECT count(*) FROM fact_sales_order", ""))
    server._run_eval()
    out = server.json.load(open(tmp_path / "e.json"))
    assert out["items"][0]["gold"] == 4731 and out["items"][0]["arms"]["C"]["correct"] is True
