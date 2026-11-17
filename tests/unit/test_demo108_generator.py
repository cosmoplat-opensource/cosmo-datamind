"""108 表演示库由业务过程仿真生成(DR-076):生成即校验、同种子可复现,校验器能发现植入的业务矛盾。"""
from __future__ import annotations

import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LAYOUT = ROOT / "examples" / "demo_metrics.db"
sys.path.insert(0, str(ROOT / "scripts"))


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    if not LAYOUT.is_file():
        pytest.skip("examples/demo_metrics.db 不在")
    import build_demo_108_db as gen
    out = tmp_path_factory.mktemp("d108")
    report = gen.build(LAYOUT, out / "demo_metrics.db", out / "report.json", force=True, seed=gen.DEFAULT_SEED)
    return gen, out / "demo_metrics.db", report


def test_build_passes_all_business_checks(built):
    _gen, db, report = built
    assert report["ok"] and report["tables"] == 108
    assert all(v["pass"] for v in report["checks"].values())
    for name in ("sales.invoice_vat_13pct", "quality.result_matches_recorded_value", "time.no_events_after_window"):
        assert name in report["checks"], name
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    assert list(con.execute("PRAGMA foreign_key_check")) == []
    fk = {r[2]: r[3] for r in con.execute("PRAGMA foreign_key_list(fact_material_consumption)")}
    assert fk.get("dim_material") == "material_id"          # 原表结构把物料列错连到产品表,已修正


def test_same_seed_reproduces_the_same_database(built, tmp_path):
    gen, _db, report = built
    again = gen.build(LAYOUT, tmp_path / "again.db", tmp_path / "again.json", force=True, seed=gen.DEFAULT_SEED)
    assert again["database_sha256"] == report["database_sha256"]


def test_checker_catches_planted_contradictions(built, tmp_path):
    _gen, db, _report = built
    import demo108_checks as chk
    copy = tmp_path / "broken.db"
    shutil.copy(db, copy)
    con = sqlite3.connect(copy)
    con.execute("UPDATE fact_delivery SET delivery_date = (SELECT date(order_date, '-1 day') FROM fact_sales_order o "
                "WHERE o.order_id = fact_delivery.order_id) WHERE delivery_id = 1")
    con.execute("UPDATE fact_invoice SET tax_amount = tax_amount + 5 WHERE invoice_id = 1")
    con.execute("UPDATE fact_quality_inspection SET result = '合格' WHERE inspection_id = "
                "(SELECT inspection_id FROM fact_quality_inspection WHERE result = '不合格' LIMIT 1)")
    con.commit()
    result = chk.run(con)
    for name in ("sales.delivery_after_order", "sales.invoice_vat_13pct", "quality.result_matches_recorded_value"):
        assert not result[name]["pass"], name
