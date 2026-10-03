"""200 表合成验证库生成器 v2(DR-075):生成即校验;独立审计发现的问题都成为自动检查,且校验器能发现植入的不一致。"""
from __future__ import annotations

import importlib.util
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "examples" / "demo_metrics.db"
sys.path.insert(0, str(ROOT / "scripts"))


def _load_generator():
    spec = importlib.util.spec_from_file_location("build_contract_200_db", ROOT / "scripts" / "build_contract_200_db.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["build_contract_200_db"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    if not BASE.is_file():
        pytest.skip("examples/demo_metrics.db 不在")
    gen = _load_generator()
    out = tmp_path_factory.mktemp("c200")
    report = gen.build(BASE, out / "contract_200.db", out / "validation.json", out / "dictionary.csv", force=True, seed=gen.DEFAULT_SEED)
    return gen, out / "contract_200.db", report


def test_build_passes_structure_and_semantic_checks(generated):
    gen, db, report = generated
    assert report["ok"] and report["tables"] == 200 and report["columns"] >= 5000
    assert all(v["pass"] for v in report["semantic_checks"].values())
    for name in ("cross_entity.shipment_line_product_matches_order_line", "cross_entity.sensor_mounted_on_equipment",
                 "header_lines.fact_shipment.amount_equals_line_sum", "values.placeholder_columns",
                 "values.measure_sequences_repeated_across_tables", "metadata.untranslated_column_names", "cases.claims_match_data"):
        assert name in report["semantic_checks"], name


def test_base_defects_registered_not_masked(generated):
    _gen, db, report = generated
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    violations = {}
    for row in con.execute("PRAGMA foreign_key_check"):
        violations[row[0]] = violations.get(row[0], 0) + 1
    cases = report["validation_cases"]
    assert violations["fact_material_consumption"] == cases["BASE_ORPHAN_CONSUMPTION_ORDER"] > 0
    assert set(violations) == {"fact_return", "fact_invoice", "fact_ar_aging", "fact_material_consumption"}
    open_issues = con.execute("SELECT COUNT(*) FROM fact_data_quality_issue WHERE rule_code LIKE 'BASE_%' AND resolution_status='OPEN'").fetchone()[0]
    assert open_issues == sum(1 for k in cases if k.startswith("BASE_"))


def test_validation_results_are_real(generated):
    _gen, db, _report = generated
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    bad = con.execute("SELECT COUNT(*) FROM fact_validation_case_result r JOIN dim_validation_case c USING(validation_case_id) "
                      "WHERE r.actual_value <> CAST(c.expected_count AS TEXT) OR r.resolution_status <> 'PASSED'").fetchone()[0]
    assert bad == 0
    dup = con.execute("SELECT COUNT(*) FROM (SELECT shipment_id, line_no FROM fact_shipment_line GROUP BY 1,2 HAVING COUNT(*)>1)").fetchone()[0]
    assert dup == 0                                      # COMPOSITE_KEY 的自述与数据一致


def test_checker_catches_planted_inconsistencies(generated, tmp_path):
    gen, db, _report = generated
    import contract_200_checks as chk
    specs = {s.name: s for s in gen.table_specs()}
    copy = tmp_path / "broken.db"
    shutil.copy(db, copy)
    con = sqlite3.connect(copy)
    con.execute("UPDATE fact_shipment_line SET prod_id = (SELECT MAX(prod_id) FROM dim_product) + 0 WHERE shipment_line_id = "
                "(SELECT shipment_line_id FROM fact_shipment_line l JOIN dim_product p ON l.prod_id=p.prod_id "
                " WHERE l.prod_id <> (SELECT MAX(prod_id) FROM dim_product) LIMIT 1)")
    con.execute("UPDATE fact_lead SET next_action = 'fact_lead:next_action:0001' WHERE lead_id = 1")
    con.execute("UPDATE fact_shipment SET amount = amount + 100 WHERE shipment_id = 1")
    con.commit()
    result = chk.run(con, specs)
    assert not result["cross_entity.shipment_line_product_matches_order_line"]["pass"]
    assert not result["values.placeholder_columns"]["pass"]
    assert not result["header_lines.fact_shipment.amount_equals_line_sum"]["pass"]
