"""Adversarial fixtures for evidence completeness, compound keys and output safety."""
import copy
import sqlite3

import pytest

import build_quality
import dao_core
import quick_build
from sqlite_evidence import relation_signals


def _pair(ir, child="orders", parent="customers"):
    return [r for r in ir["relations"]
            if (r["source_concept"], r["target_concept"]) == (child, parent)]


@pytest.mark.parametrize("child,parent", [
    ("customer_id,line_no", "id,unrelated"),
    ("customer_id,line_no", "id"),
    ("customer_id,", "customer_id,"),
    ("id,id", "id,id"),
    ("", ""),
])
def test_composite_name_evidence_cannot_bypass_component_validation(child, parent):
    assert dao_core.name_ok(child, "customers", parent) is False


def test_missing_parent_table_is_not_positive_name_evidence():
    assert dao_core.name_ok("order_id", "", "customer_id") is False


@pytest.mark.parametrize("overlap", [float("inf"), 100.01, "100", 10 ** 1000])
def test_invalid_overlap_never_verifies(overlap):
    assert dao_core.classify(overlap=overlap, parent_unique=True, name_ok=True,
                             child_distinct=10)["status"] != "verified"


def test_composite_primary_key_component_is_not_assumed_unique(make_sqlite, tmp_path):
    db = make_sqlite({
        "customers": ("customer_id INTEGER, region_id INTEGER, PRIMARY KEY(customer_id, region_id)",
                      [(1, 1), (1, 2), (2, 1)]),
        "orders": ("order_id INTEGER, customer_id INTEGER", [(10, 1), (11, 1), (12, 2)]),
    })
    ir = quick_build.build(db, str(tmp_path / "ir.json"), "compound")
    assert not any(r["status"] == "verified" for r in _pair(ir))


def test_declared_composite_fk_is_one_ordered_joint_relationship(make_sqlite, tmp_path):
    db = make_sqlite({
        "customers": ("customer_id INTEGER, region_id INTEGER, PRIMARY KEY(region_id, customer_id)",
                      [(1, 1), (1, 2), (2, 1)]),
        "orders": ("order_id INTEGER, customer_id INTEGER, region_id INTEGER, "
                   "FOREIGN KEY(region_id, customer_id) REFERENCES customers",
                   [(10, 1, 1), (11, 1, 2)]),
    })
    ir = quick_build.build(db, str(tmp_path / "ir.json"), "compound")
    relations = _pair(ir)
    assert len(relations) == 1
    evidence = relations[0]["evidence"]
    assert evidence["child_key"] == "region_id,customer_id"
    assert evidence["parent_key"] == "region_id,customer_id"
    assert relations[0]["status"] == "verified"
    assert ir["build_quality"]["evidence"]["valid"] is True


def test_declared_fk_with_orphans_does_not_claim_verified(make_sqlite, tmp_path):
    db = make_sqlite({
        "customers": ("customer_id INTEGER PRIMARY KEY", [(1,), (2,)]),
        "orders": ("order_id INTEGER, customer_id INTEGER REFERENCES customers(customer_id)",
                   [(10, 1), (11, 999)]),
    })
    ir = quick_build.build(db, str(tmp_path / "ir.json"), "orphan")
    relation = _pair(ir)[0]
    assert relation["status"] == "candidate"
    assert relation["evidence"]["orphan_count"] == 1


def test_distinct_prefix_cannot_hide_majority_orphans(make_sqlite, tmp_path):
    db = make_sqlite({
        "customers": ("customer_id INTEGER PRIMARY KEY", [(i,) for i in range(20_000)]),
        "orders": ("customer_id INTEGER", [(i,) for i in range(50_000)] + [(0,)]),
    })
    ir = quick_build.build(db, str(tmp_path / "ir.json"), "tail")
    relation = _pair(ir)[0]
    assert relation["status"] == "candidate"
    assert relation["overlap"] == 40.0
    assert relation["evidence"]["child_distinct"] == 50_000
    assert relation["evidence"]["evidence_complete"] is True


def test_matching_values_after_parent_prefix_are_not_missed(make_sqlite, tmp_path):
    db = make_sqlite({
        "customers": ("customer_id INTEGER PRIMARY KEY", [(i,) for i in range(25_000)]),
        "orders": ("customer_id INTEGER", [(24_998,), (24_999,), (24_999,)]),
    })
    ir = quick_build.build(db, str(tmp_path / "ir.json"), "late-values")
    relations = _pair(ir)
    assert relations and relations[0]["status"] == "verified"
    assert relations[0]["overlap"] == 100.0


def test_build_rejects_output_aliasing_source_without_modifying_database(make_sqlite):
    db = make_sqlite({"customers": ("id INTEGER PRIMARY KEY", [(1,)])})
    before = open(db, "rb").read()
    with pytest.raises(ValueError):
        quick_build.build(db, db, "overwrite")
    assert open(db, "rb").read() == before


def test_build_accepts_sqlite_filename_with_uri_metacharacters(make_sqlite, tmp_path):
    db = make_sqlite({"customers": ("id INTEGER PRIMARY KEY", [(1,)])}, fname="fx?mode=rw#source.db")
    ir = quick_build.build(db, str(tmp_path / "ir.json"), "uri")
    assert len(ir["objects"]) == 1


def test_output_parent_reference_is_rejected_before_normalization(make_sqlite, tmp_path):
    db = make_sqlite({"customers": ("id INTEGER PRIMARY KEY", [(1,)])})
    with pytest.raises(ValueError):
        quick_build.build(db, str(tmp_path / "unused" / ".." / "ir.json"), "traversal")


def test_failed_build_closes_connection(make_sqlite, tmp_path, monkeypatch):
    db = make_sqlite({"customers": ("id INTEGER PRIMARY KEY", [(1,)])})

    def fail(*args, **kwargs):
        raise RuntimeError("forced normalization failure")

    monkeypatch.setattr(quick_build.content_quality, "normalize_kind", fail)
    with pytest.raises(RuntimeError):
        quick_build.build(db, str(tmp_path / "ir.json"), "failure")
    assert quick_build.con is None


def _verified_ir():
    return {"relations": [{"source_concept": "orders", "target_concept": "customers", "status": "verified",
                            "evidence": {"child_key": "customer_id", "parent_key": "customer_id",
                                         "overlap": 100.0, "parent_unique": True, "name_ok": True,
                                         "direction": "child_to_parent"}}]}


@pytest.mark.parametrize("overlap", [float("nan"), float("inf"), 100.01, 10 ** 1000])
def test_evidence_audit_rejects_non_percentage_values(overlap):
    ir = _verified_ir()
    ir["relations"][0]["evidence"]["overlap"] = overlap
    assert build_quality.audit_verified_evidence(ir)["valid"] is False


def test_note_text_does_not_grant_schema_evidence_exemption():
    ir = _verified_ir()
    ir["relations"][0]["note"] = "声明FK made-up metadata"
    ir["relations"][0]["evidence"] = {"child_key": "customer_id", "parent_key": "id"}
    assert build_quality.audit_verified_evidence(ir)["valid"] is False


@pytest.mark.parametrize("update", [
    {"child_key": "customer_id,region_id"},
    {"evidence_complete": False},
    {"child_key": "customer_id,"},
    {"child_key": "customer_id,customer_id", "parent_key": "customer_id,customer_id"},
])
def test_evidence_audit_rejects_incomplete_or_invalid_join_keys(update):
    ir = copy.deepcopy(_verified_ir())
    ir["relations"][0]["evidence"].update(update)
    assert build_quality.audit_verified_evidence(ir)["valid"] is False


def test_full_tuple_overlap_requires_matching_pairs(make_sqlite):
    db = make_sqlite({
        "parents": ("a INTEGER, b INTEGER", [(1, 2), (2, 1)]),
        "children": ("a INTEGER, b INTEGER", [(1, 1), (2, 2)]),
    })
    with sqlite3.connect(db) as con:
        result = relation_signals(con, "children", ("a", "b"), "parents", ("a", "b"))
    assert result["overlap"] == 0.0
    assert result["child_distinct"] == result["parent_distinct"] == 2
    assert result["child_unique"] is result["parent_unique"] is True


def test_explicit_five_column_tuple_and_fk_are_supported(make_sqlite, tmp_path):
    columns = ("a", "b", "c", "d", "e")
    db = make_sqlite({
        "parents": ("a INTEGER, b INTEGER, c INTEGER, d INTEGER, e INTEGER, PRIMARY KEY(a,b,c,d,e)",
                    [(1, 2, 3, 4, 5), (1, 2, 3, 4, 6)]),
        "children": ("a INTEGER, b INTEGER, c INTEGER, d INTEGER, e INTEGER, "
                     "FOREIGN KEY(a,b,c,d,e) REFERENCES parents", [(1, 2, 3, 4, 5)]),
    })
    with sqlite3.connect(db) as con:
        result = relation_signals(con, "children", columns, "parents", columns)
    assert result["overlap"] == 100.0
    ir = quick_build.build(db, str(tmp_path / "ir.json"), "five-column")
    relation = _pair(ir, "children", "parents")[0]
    assert relation["status"] == "verified"
    assert relation["evidence"]["parent_key"] == "a,b,c,d,e"


@pytest.mark.parametrize("columns", [("missing",), ("id", "id"), ("",), ("id",) * 33])
def test_missing_and_invalid_columns_do_not_become_sqlite_string_literals(make_sqlite, columns):
    db = make_sqlite({"parents": ("id INTEGER", [(1,), (2,)])})
    with sqlite3.connect(db) as con, pytest.raises(ValueError):
        relation_signals(con, "parents", columns, "parents", columns)


def test_sql_evidence_preserves_callers_progress_budget(make_sqlite):
    db = make_sqlite({"parents": ("id INTEGER", [(1,), (2,)])})
    with sqlite3.connect(db) as con:
        con.set_progress_handler(lambda: 1, 1)
        with pytest.raises(sqlite3.OperationalError, match="interrupted"):
            relation_signals(con, "parents", ("id",), "parents", ("id",))
        with pytest.raises(sqlite3.OperationalError, match="interrupted"):
            con.execute("SELECT 1")


def test_nullable_primary_key_is_not_a_complete_candidate_key(make_sqlite, tmp_path):
    # SQLite ordinary rowid tables permit NULL in a non-INTEGER PRIMARY KEY.
    db = make_sqlite({
        "customers": ("customer_id TEXT PRIMARY KEY", [("a",), ("b",), (None,), (None,)]),
        "orders": ("customer_id TEXT", [("a",), ("a",), ("b",)]),
    })
    ir = quick_build.build(db, str(tmp_path / "ir.json"), "null-pk")
    assert all(r["status"] != "verified" for r in _pair(ir))


def test_output_hardlink_to_source_is_rejected(make_sqlite, tmp_path):
    import os
    db = make_sqlite({"customers": ("id INTEGER PRIMARY KEY", [(1,)])})
    alias = tmp_path / "alias.json"
    os.link(db, alias)
    with pytest.raises(ValueError):
        quick_build.build(db, str(alias), "hardlink")


def test_declared_fk_schema_mismatch_requires_review(make_sqlite, tmp_path):
    db = make_sqlite({
        "customers": ("customer_id INTEGER", [(1,), (1,)]),
        "orders": ("customer_id INTEGER REFERENCES customers(customer_id)", [(1,), (1,)]),
    })
    ir = quick_build.build(db, str(tmp_path / "ir.json"), "bad-fk")
    relation = _pair(ir)[0]
    assert relation["status"] == "candidate"
    assert relation["evidence"]["schema_validated"] is False
    assert ir["scenario"]["query_errors"][0]["operation"] == "foreign_key_check"


def test_budget_exhaustion_closes_snapshot_without_publishing_partial_graph(make_sqlite, tmp_path, monkeypatch):
    db = make_sqlite({"customers": ("id INTEGER PRIMARY KEY", [(1,)])})
    out = tmp_path / "ir.json"
    monkeypatch.setattr(quick_build, "_BUILD_TIMEOUT_SECONDS", 0)
    with pytest.raises((TimeoutError, sqlite3.OperationalError)):
        quick_build.build(db, str(out), "budget")
    assert quick_build.con is None
    assert not out.exists()
