"""DR-056: fresh evidence, replayable references and fail-closed metric comparison."""
from copy import deepcopy
import sqlite3

import pytest

import metric_contract as MC
import metric_pipeline as MP


def contract(**fields):
    return MC.normalize({"id": "m", "name": "total", "table": "sales",
                         "measure": {"col": "amount", "agg": "sum"}, **fields})


@pytest.mark.parametrize("status", ["deprecated", "certified"])
def test_mining_cannot_overwrite_human_decision_or_attach_other_formula_evidence(status):
    old = contract(status=status, certified_by="reviewer")
    old["evidence"] = {"compiled_sql": "original evidence"}
    changed = contract(status="verified", measure={"col": "amount", "agg": "avg"})
    changed["evidence"] = {"compiled_sql": "evidence for AVG", "match": True}
    layers, _ = MC.upsert_layers({"metric_layers": {"atomic": [old]}}, [changed])
    saved = layers["atomic"][0]
    assert saved["status"] == status
    assert saved["measure"] == old["measure"]
    assert saved["evidence"] == old["evidence"]


def test_new_failed_adjudication_revokes_stale_verified_status():
    old = contract(status="verified")
    fresh = contract(status="candidate", evidence={"executed": True, "match": False})
    layers, _ = MC.upsert_layers({"metric_layers": {"atomic": [old]}}, [fresh])
    assert layers["atomic"][0]["status"] == "candidate"


@pytest.mark.parametrize("kind", ["value", "sql"])
def test_saved_reference_can_replay_without_provenance(make_sqlite, kind):
    db = make_sqlite({"sales": ("amount REAL", [(10,), (20,)])})
    ref = {"kind": kind, "source": "acceptance", "tol": 0.0}
    ref.update({"value": 30} if kind == "value" else {"sql": "SELECT SUM(amount) FROM sales"})
    first = MC.adjudicate(db, contract(), references=[ref])
    assert first["status"] == "verified"
    result = MP.readjudicate(db, {"metric_layers": {"atomic": [first]}})
    assert result["report"]["verified"] == 1
    assert result["metric_layers"]["atomic"][0]["evidence"]["match"] is True


@pytest.mark.parametrize("mine,reference,tol", [
    ([[0.0000001]], [[0.0000002]], 0.0),
    ([["001", 10]], [["1", 10]], 0.01),
    ([[100, 10]], [[101, 10]], 0.02),
    ([[None]], [["None"]], 0.01),
    ([[float("inf")]], [[100]], 0.01),
    ([[1]], [[2]], float("inf")),
    ([[1]], [[1]], -1),
    ([[1]], [[1]], "invalid"),
    ([[1], [1, 2]], [[1], [1, 3]], 0.01),
    ([[9007199254740992]], [[9007199254740993]], 0.0),
])
def test_comparison_cannot_manufacture_agreement(mine, reference, tol):
    assert not MC.compare_results({"rows": mine}, {"rows": reference}, tol)[0]


def test_result_limit_is_not_complete_evidence(make_sqlite):
    db = make_sqlite({"sales": ("amount REAL", [(10,), (20,), (30,)])})
    truncated = MC.execute(db, "SELECT amount FROM sales", limit=2)
    assert truncated.get("truncated") is True
    assert not MC.compare_results(truncated, deepcopy(truncated))[0]


@pytest.mark.parametrize("sql", ["PRAGMA user_version", "ATTACH DATABASE ':memory:' AS other"])
def test_metric_executor_only_accepts_queries(make_sqlite, sql):
    db = make_sqlite({"sales": ("amount REAL", [(10,)])})
    assert MC.execute(db, sql).get("error")


def test_compile_revalidates_raw_contract_before_using_filter_operator():
    raw = contract()
    raw["filters"] = [{"col": "amount", "op": "IS NULL OR 1=1 --", "value": 0}]
    with pytest.raises(ValueError):
        MC.compile_sql(raw)


def test_invalid_count_column_is_not_silently_changed_to_count_star():
    with pytest.raises(ValueError):
        contract(measure={"col": "amount;--", "agg": "count"})


def test_reference_dimension_must_exist_before_sqlite_execution(make_sqlite):
    db = make_sqlite({"sales": ("amount REAL", [(10,), (20,)])})
    ref = {"kind": "sql", "sql": "SELECT 'ghost', SUM(amount) FROM sales", "dimensions": ["ghost"]}
    result = MC.adjudicate(db, contract(), references=[ref])
    assert result["status"] == "candidate"
    assert "列不存在" in result["evidence"]["reference"]["note"]


@pytest.mark.parametrize("agg", ["min", "max"])
def test_text_extrema_can_be_verified_by_exact_reference(make_sqlite, agg):
    db = make_sqlite({"sales": ("day TEXT", [("2026-09-01",), ("2026-09-07",)])})
    c = contract(measure={"col": "day", "agg": agg})
    result = MC.adjudicate(db, c, references=[{"kind": "sql", "sql": f"SELECT {agg}(day) FROM sales"}])
    assert result["status"] == "verified"


def test_invalid_existing_contract_cannot_keep_stale_verified(make_sqlite):
    db = make_sqlite({"sales": ("amount REAL", [(10,)])})
    old = contract(status="verified", candidate=False)
    old["filters"] = [{"col": "amount", "op": "unsupported", "value": 10}]
    result = MP.readjudicate(db, {"metric_layers": {"atomic": [old]}})
    saved = result["metric_layers"]["atomic"][0]
    assert saved["status"] == "candidate"
    assert saved["candidate"] is True and saved["evidence"]["error"]


def test_adjudication_cannot_compare_values_from_different_snapshots(make_sqlite, monkeypatch):
    db = make_sqlite({"sales": ("amount REAL", [(10,)])})
    with sqlite3.connect(db) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
    original = MC.execute
    executions = 0

    def execute_then_mutate(*args, **kwargs):
        nonlocal executions
        result = original(*args, **kwargs)
        executions += 1
        if executions == 1:
            # Before: SUM=10, COUNT=1. After: SUM=20, COUNT=10.
            # Cross-snapshot SUM=10 / COUNT=10 must never become verified.
            with sqlite3.connect(db) as writer:
                writer.execute("DELETE FROM sales")
                writer.executemany("INSERT INTO sales VALUES (?)", [(2,)] * 10)
        return result

    monkeypatch.setattr(MC, "execute", execute_then_mutate)
    result = MC.adjudicate(db, contract(), references=[
        {"kind": "sql", "sql": "SELECT COUNT(*) FROM sales", "tol": 0},
    ])
    assert executions == 2
    assert result["status"] == "candidate"
    assert result["evidence"]["match"] is False
