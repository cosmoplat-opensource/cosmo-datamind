# -*- coding: utf-8 -*-
"""证据强度的几处漏洞(DR-099):只有一个取值的子键被判 verified;指标的独立参照值排在自参照之后、
永远比不到;过滤条件不同的指标共用一个 id;人审否决的关系模式读不到构建产物的字段名。"""
import dao_core
import metric_contract as MC
import metric_mining as MM
import server

TAB = {"orders": [("id", "INTEGER"), ("amount", "REAL"), ("status", "TEXT")]}


# ── 关系裁决 ────────────────────────────────────────────────────────

def _classify(child_distinct):
    return dao_core.classify(overlap=100.0, parent_unique=True, name_ok=True, child_distinct=child_distinct,
                             min_distinct=1, exclude_pk_child=False)


def test_a_child_key_with_a_single_value_is_not_verified():
    got = _classify(1)
    assert got["status"] == "candidate" and "一个取值" in got["reason"]


def test_two_distinct_values_can_still_be_verified():
    assert _classify(2)["status"] == "verified"


# ── 指标核验 ────────────────────────────────────────────────────────

def _gold_candidate(make_sqlite, gold):
    db = make_sqlite({"orders": ("id INTEGER, amount REAL, status TEXT", [(1, 60.0, "paid"), (2, 40.0, "paid")])})
    cands, _ = MM.from_gold_set([{"id": "g1", "gold_sql": "SELECT SUM(amount) AS total FROM orders", "gold": gold}], TAB)
    c = cands[0]
    return MC.adjudicate(db, c, references=c["references"])


def test_an_independent_reference_value_that_disagrees_blocks_verification(make_sqlite):
    got = _gold_candidate(make_sqlite, 99999)
    assert got["status"] == "candidate"
    assert got["evidence"]["reference"]["kind"] == "value" and got["evidence"]["match"] is False


def test_an_independent_reference_value_that_agrees_is_the_recorded_evidence(make_sqlite):
    got = _gold_candidate(make_sqlite, 100)
    assert got["status"] == "verified" and got["evidence"]["reference"]["kind"] == "value"


def test_metrics_with_different_filters_get_different_ids_and_both_survive():
    paid, _ = MM.parse_sql("SELECT SUM(amount) AS paid_amt FROM orders WHERE status = 'paid'", "a.sql", TAB)
    void, _ = MM.parse_sql("SELECT SUM(amount) AS void_amt FROM orders WHERE status = 'void'", "b.sql", TAB)
    plain, _ = MM.parse_sql("SELECT SUM(amount) AS all_amt FROM orders", "c.sql", TAB)
    ids = {paid[0]["id"], void[0]["id"], plain[0]["id"]}
    assert len(ids) == 3 and plain[0]["id"] == "mine.orders.amount.sum"
    again, _ = MM.parse_sql("SELECT SUM(amount) AS x FROM orders WHERE status = 'paid'", "d.sql", TAB)
    assert again[0]["id"] == paid[0]["id"]                      # 同一过滤条件的 id 稳定
    layers, _stat = MC.upsert_layers({}, paid + void)
    assert {m["name"] for m in layers["atomic"]} == {"paid_amt", "void_amt"}


# ── 否决模式回流 ────────────────────────────────────────────────────

def test_rejected_patterns_are_read_from_built_graphs(monkeypatch):
    built = {"objects": [{"name": "订单", "table": "t_order"}, {"name": "客户", "table": "t_customer"}],
             "relations": [{"source_concept": "订单", "target_concept": "客户", "verb": "属于",
                            "human_review": "rejected", "review_reason": "订单不归属客户主体"}]}
    demo = {"objects": [{"id": "a", "table": "ta"}, {"id": "b", "table": "tb"}],
            "links": [{"source": "a", "target": "b", "verb": "引用", "human_review": "rejected"}]}
    monkeypatch.setattr(server, "_iter_all_irs", lambda: iter([("built_x", built), ("demo", demo)]))
    pats = server._rejected_patterns()
    assert any("t_order" in p and "t_customer" in p and "订单不归属客户主体" in p for p in pats)
    assert any("ta" in p and "tb" in p for p in pats)
