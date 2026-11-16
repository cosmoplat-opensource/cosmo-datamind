# -*- coding: utf-8 -*-
"""算术见证(DR-135):同一行里一列等于另外两列的和、差、积、商,在数据上逐行成立时记为证据。

用途:派生公式的提议能否在数据上得到核验;问数时告诉模型哪些列之间有确定的算术关系。"""
import sqlite3

import arithmetic_witness as W


def _db(make_sqlite, rows=None):
    rows = rows or [(i, 10.0 + i, 2.0 + i % 3, (10.0 + i) * (2.0 + i % 3), 5.0 + i, (10.0 + i) * (2.0 + i % 3) - (5.0 + i), 7, 0)
                    for i in range(60)]
    return make_sqlite({"orders": ("id INTEGER, price REAL, qty REAL, amount REAL, cost REAL, profit REAL, k INTEGER, z INTEGER", rows)})


def test_row_level_identities_are_found_with_their_coverage(make_sqlite):
    con = sqlite3.connect(_db(make_sqlite))
    found = {(w["column"], w["formula"]) for w in W.discover(con, "orders")}
    assert ("amount", "price * qty") in found
    assert ("profit", "amount - cost") in found
    w = next(x for x in W.discover(con, "orders") if x["column"] == "profit")
    assert w["rows"] == 60 and w["matched"] == 60 and w["inputs"] == ["amount", "cost"]


def test_trivial_and_coincidental_relations_are_not_reported(make_sqlite):
    con = sqlite3.connect(_db(make_sqlite))
    formulas = [w["formula"] for w in W.discover(con, "orders")]
    assert not any("z" in f.split() or "k" in f.split() for f in formulas)     # 常数列、全零列不参与
    assert ("cost", "amount - profit") not in {(w["column"], w["formula"]) for w in W.discover(con, "orders")}   # 同一个等式只报一次
    assert all(w["column"] not in w["inputs"] for w in W.discover(con, "orders"))


def test_a_relation_that_fails_on_some_rows_is_not_a_witness(make_sqlite):
    rows = [(i, 10.0 + i, 2.0, (10.0 + i) * 2.0 + (50 if i % 4 == 0 else 0), 1.0 + i, 0, 7, 0) for i in range(60)]
    con = sqlite3.connect(_db(make_sqlite, rows))
    assert ("amount", "price * qty") not in {(w["column"], w["formula"]) for w in W.discover(con, "orders")}


def test_too_few_rows_give_no_witness(make_sqlite):
    rows = [(i, 10.0 + i, 2.0, (10.0 + i) * 2.0, 1.0 + i, 0, 7, 0) for i in range(5)]
    con = sqlite3.connect(_db(make_sqlite, rows))
    assert W.discover(con, "orders") == []


def test_a_proposed_formula_is_checked_against_the_witnesses():
    witnesses = [{"table": "orders", "column": "profit", "formula": "amount - cost", "op": "-", "inputs": ["amount", "cost"],
                  "rows": 60, "matched": 60}]
    assert W.supports("orders.amount - orders.cost", witnesses)["column"] == "profit"
    assert W.supports("销售金额 减去 成本(amount - cost)", witnesses)["column"] == "profit"
    assert W.supports("amount + cost", witnesses) is None
    assert W.supports("cost - amount", witnesses) is None


def test_brief_lines_for_the_question_context():
    witnesses = [{"table": "orders", "column": "profit", "formula": "amount - cost", "op": "-", "inputs": ["amount", "cost"],
                  "rows": 60, "matched": 60}]
    assert W.brief(witnesses, ["orders"]) == ["orders.profit = amount - cost(数据核验:60/60 行)"]
    assert W.brief(witnesses, ["other"]) == []


def test_columns_that_are_mostly_zero_do_not_make_a_witness(make_sqlite):
    """0 = 0 × x、0 = a − a 这类行对什么关系都成立,不能当证据(示例库里的账龄列就是这样)。"""
    rows = [(i, 100 + i, 100 + i, (3.0 if i == 7 else 0.0), (2.0 if i == 7 else 0.0), (6.0 if i == 7 else 0.0)) for i in range(80)]
    con = sqlite3.connect(make_sqlite({"aging": ("id INTEGER, ar_id INTEGER, invoice_id INTEGER, m1 REAL, m2 REAL, m5 REAL", rows)}))
    assert W.discover(con, "aging") == []


# ── 接入构建与问数 ───────────────────────────────────────────────────────────

def test_the_build_records_witnesses_and_verifies_matching_derived_formulas(make_sqlite):
    import server
    db = _db(make_sqlite)
    ir = {"objects": [{"id": "orders", "table": "orders"}],
          "derived": [{"name": "利润", "formula": "amount - cost", "evidence": {"inputs_bound": True, "formula_verified": False}},
                      {"name": "利润率", "formula": "(amount - cost) / amount", "evidence": {"formula_verified": False}},
                      {"name": "错的", "formula": "amount + cost", "evidence": {"formula_verified": False}}]}
    n, verified = server._attach_arithmetic(db, ir)
    assert n == 2 and verified == 1
    assert {(w["column"], w["formula"]) for w in ir["arithmetic"]} == {("amount", "price * qty"), ("profit", "amount - cost")}
    d = ir["derived"]
    assert d[0]["evidence"]["formula_verified"] is True and d[0]["evidence"]["arithmetic_witness"]["column"] == "profit"
    assert d[1]["evidence"]["formula_verified"] is False and d[2]["evidence"]["formula_verified"] is False


def test_the_question_context_states_verified_formulas_and_the_check_status():
    import workload_concepts
    ir = {"concepts": [{"name": "金额", "table": "orders", "column": "amount"}, {"name": "成本", "table": "orders", "column": "cost"}],
          "derived": [{"name": "利润", "formula": "amount - cost", "uses": ["金额", "成本"],
                       "evidence": {"formula_verified": True, "arithmetic_witness": {"table": "orders", "column": "profit",
                                                                                     "matched": 60, "rows": 60}}}]}
    text, _n = workload_concepts.context("利润是多少", ir)
    assert "数据核验:等于 orders.profit(60/60 行)" in text and "尚未执行核验" not in text


def test_the_question_context_lists_arithmetic_relations_of_the_shown_tables():
    import server
    lines = server._arithmetic_lines({"arithmetic": [{"table": "orders", "column": "profit", "formula": "amount - cost", "op": "-",
                                                      "inputs": ["amount", "cost"], "rows": 60, "matched": 60}]}, ["orders"])
    assert lines == "列之间的算术关系(逐行数据核验,可直接使用对应的列):\norders.profit = amount - cost(数据核验:60/60 行)"
    assert server._arithmetic_lines({"arithmetic": []}, ["orders"]) == ""


def test_a_formula_with_more_terms_is_not_supported_by_a_two_column_witness():
    witnesses = [{"table": "orders", "column": "profit", "formula": "amount - cost", "op": "-", "inputs": ["amount", "cost"],
                  "rows": 60, "matched": 60}]
    assert W.supports("(amount - cost) / amount", witnesses) is None
    assert W.supports("amount - cost - discount", witnesses) is None
