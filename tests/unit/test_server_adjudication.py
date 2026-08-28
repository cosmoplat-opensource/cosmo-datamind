# -*- coding: utf-8 -*-
"""会话式 LLM 构建分支必须与 quick_build 共用同一数据裁决口径。"""
import server


def _evidence(columns):
    return {"tab_cols": {table: [(col, "INTEGER") for col in cols] for table, cols in columns.items()},
            "schema": "", "docs": "", "n_docs": 0, "refs": []}


def _proposal(child_key, parent_key):
    return {
        "objects": [
            {"name": "orders", "cn": "订单", "table": "orders", "kind": "ice"},
            {"name": "customers", "cn": "客户", "table": "customers", "kind": "object"},
        ],
        "relations": [{"source": "orders", "target": "customers", "verb": "属于",
                       "rationale": "schema 候选", "child_key": child_key, "parent_key": parent_key}],
    }


def test_llm_branch_verifies_with_replayable_core_signals(make_sqlite, monkeypatch):
    db = make_sqlite({
        "customers": ("id INTEGER PRIMARY KEY", [(1,), (2,), (3,)]),
        "orders": ("order_id INTEGER PRIMARY KEY, customer_id INTEGER", [(10, 1), (11, 1), (12, 2)]),
    })
    monkeypatch.setattr(server, "_llm_semantic_review", lambda relations, ev: None)
    ir = server._adjudicate_ir(db, "test", _proposal("customer_id", "id"),
                               _evidence({"customers": ["id"], "orders": ["order_id", "customer_id"]}))
    relation = ir["relations"][0]
    assert relation["status"] == "verified"
    assert relation["evidence"]["parent_unique"] is True
    assert relation["evidence"]["name_ok"] is True
    assert relation["evidence"]["direction"] == "child_to_parent"
    assert relation["proposal"]["child_key_hint"] == "customer_id"


def test_llm_branch_suppresses_reverse_direction(make_sqlite, monkeypatch):
    db = make_sqlite({
        "customers": ("id INTEGER", [(1,), (1,), (2,)]),
        "orders": ("order_id INTEGER PRIMARY KEY", [(1,), (2,), (3,)]),
    })
    monkeypatch.setattr(server, "_llm_semantic_review", lambda relations, ev: None)
    ir = server._adjudicate_ir(db, "test", _proposal("order_id", "id"),
                               _evidence({"customers": ["id"], "orders": ["order_id"]}))
    relation = ir["relations"][0]
    assert relation["status"] == "candidate"
    assert relation["evidence"]["direction"] == "reverse"
    assert "方向反证" in relation["note"]


def test_generic_id_does_not_turn_unrelated_key_into_verified(make_sqlite, monkeypatch):
    db = make_sqlite({
        "customers": ("id INTEGER PRIMARY KEY", [(1,), (2,), (3,)]),
        "orders": ("order_id INTEGER PRIMARY KEY", [(1,), (2,), (3,)]),
    })
    monkeypatch.setattr(server, "_llm_semantic_review", lambda relations, ev: None)
    ir = server._adjudicate_ir(db, "test", _proposal("order_id", "id"),
                               _evidence({"customers": ["id"], "orders": ["order_id"]}))
    relation = ir["relations"][0]
    assert relation["status"] == "candidate"
    assert relation["evidence"]["name_ok"] is False


def test_three_column_composite_key_is_adjudicated_as_a_tuple(make_sqlite, monkeypatch):
    db = make_sqlite({
        "customers": (
            "country TEXT, region TEXT, number INTEGER, name TEXT, "
            "PRIMARY KEY(country, region, number)",
            [("CN", "E", 1, "A"), ("CN", "E", 2, "B"), ("JP", "K", 1, "C")],
        ),
        "orders": (
            "order_id INTEGER PRIMARY KEY, country TEXT, region TEXT, number INTEGER",
            [(10, "CN", "E", 1), (11, "CN", "E", 1), (12, "JP", "K", 1)],
        ),
    })
    monkeypatch.setattr(server, "_llm_semantic_review", lambda relations, ev: None)
    ir = server._adjudicate_ir(
        db,
        "test",
        _proposal("country,region,number", "country,region,number"),
        _evidence({
            "customers": ["country", "region", "number", "name"],
            "orders": ["order_id", "country", "region", "number"],
        }),
    )
    relation = ir["relations"][0]
    assert relation["status"] == "verified"
    assert relation["evidence"]["source"] == "composite_key"
    assert relation["evidence"]["parent_unique"] is True
    assert relation["evidence"]["child_key"] == "country,region,number"


def test_composite_uniqueness_does_not_use_delimiter_concatenation(make_sqlite, monkeypatch):
    separator = "\x1f"
    db = make_sqlite({
        "customers": (
            "left_key TEXT, right_key TEXT, PRIMARY KEY(left_key, right_key)",
            [(f"a{separator}b", "c"), ("a", f"b{separator}c")],
        ),
        "orders": (
            "order_id INTEGER PRIMARY KEY, left_key TEXT, right_key TEXT",
            [(1, f"a{separator}b", "c"), (2, "a", f"b{separator}c")],
        ),
    })
    monkeypatch.setattr(server, "_llm_semantic_review", lambda relations, ev: None)
    ir = server._adjudicate_ir(
        db,
        "test",
        _proposal("left_key,right_key", "left_key,right_key"),
        _evidence({
            "customers": ["left_key", "right_key"],
            "orders": ["order_id", "left_key", "right_key"],
        }),
    )
    assert ir["relations"][0]["status"] == "verified"
