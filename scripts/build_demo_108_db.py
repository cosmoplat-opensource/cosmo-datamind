#!/usr/bin/env python3
"""Generate the 108-table demo database by business-process simulation (DR-076).

The table and column layout is taken from the original demo database (read only), so code,
built ontologies and metric definitions keep working; the data is regenerated end to end by
``demo108_sim`` (one coherent modified-plastics business, 2025-01-01 .. 2026-06-30) and
checked by ``demo108_checks`` before the file is published.  Two schema corrections:
``fact_material_consumption.material_id`` now references ``dim_material`` (it referenced
``dim_product``), and ``DECIMAL(p,s)`` columns are declared ``REAL`` (NUMERIC affinity stored
whole-valued decimals as integers, so divisions in queries truncated).  Standard library only.

    python3 scripts/build_demo_108_db.py --layout examples/demo_metrics.db --output out/demo_metrics.db \\
        --report out/demo108_report.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import demo108_checks as CHK  # noqa: E402
import demo108_dws as DWS  # noqa: E402
import demo108_master as MASTER  # noqa: E402
import demo108_meta as META  # noqa: E402
import demo108_rest as REST  # noqa: E402
import demo108_sim as SIM  # noqa: E402

GENERATOR_VERSION = "1.0.0"
DEFAULT_SEED = 20261003
COPY_AS_IS = ("dim_metric_catalog", "dim_derived_metric_config", "dim_composite_metric_config")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def create_schema(con: sqlite3.Connection, layout: sqlite3.Connection) -> list[str]:
    tables = []
    for name, sql in layout.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        if name == "fact_material_consumption":
            sql = re.sub(r"(material_id[^,]*?REFERENCES\s+)\"?dim_product\"?\s*\(\s*\"?prod_id\"?\s*\)", r"\1dim_material(material_id)", sql)
        # DECIMAL 是 NUMERIC 亲和类型,SQLite 会把整值小数存成整数,查询时发生整数除法;小数列一律声明为 REAL
        sql = re.sub(r"\bDECIMAL\s*\(\s*\d+\s*,\s*\d+\s*\)", "REAL", sql, flags=re.I)
        con.execute(sql)
        tables.append(name)
    for (sql,) in layout.execute("SELECT sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"):
        con.execute(sql)
    return tables


def insert(con: sqlite3.Connection, table: str, rows: list[dict[str, Any]]) -> None:
    info = list(con.execute(f'PRAGMA table_info("{table}")'))
    cols = [r[1] for r in info]
    if not rows:
        return
    real = {r[1] for r in info if any(k in (r[2] or "").upper() for k in ("DECIMAL", "REAL", "FLOAT", "DOUBLE", "NUMERIC"))}
    unknown = {k for r in rows for k in r if not k.startswith("_")} - set(cols)
    if unknown:
        raise KeyError(f"{table}: generated columns not in the layout: {sorted(unknown)}")
    con.executemany(f'INSERT INTO "{table}" ({",".join(chr(34) + c + chr(34) for c in cols)}) VALUES ({",".join("?" for _ in cols)})',
                    [[float(r[c]) if c in real and isinstance(r.get(c), int) else r.get(c) for c in cols] for r in rows])   # 小数列一律存为浮点,避免 SQLite 整数除法


def build(layout: Path, output: Path, report: Path, force: bool, seed: int) -> dict[str, Any]:
    layout, output = layout.resolve(), output.resolve()
    if output == layout:
        raise ValueError("output must not overwrite the layout database")
    if output.exists() and not force:
        raise FileExistsError(f"output exists; use --force: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    report.parent.mkdir(parents=True, exist_ok=True)
    m = MASTER.build_master(seed)
    sim = SIM.Sim(m, seed)
    sim.run()
    REST.finalize(sim)
    DWS.build(sim)
    handle = tempfile.NamedTemporaryFile(prefix="demo108_", suffix=".db", dir=output.parent, delete=False)
    tmp = Path(handle.name)
    handle.close()
    con = None
    try:
        base = sqlite3.connect(f"file:{layout}?mode=ro", uri=True)
        con = sqlite3.connect(tmp)
        con.execute("PRAGMA journal_mode=DELETE")
        tables = create_schema(con, base)
        data = {**m.tables, **{k: v for k, v in sim.t.items() if not k.startswith("_")}}
        for name in tables:
            if name in COPY_AS_IS:
                rows = base.execute(f'SELECT * FROM "{name}"').fetchall()
                if rows:
                    con.executemany(f'INSERT INTO "{name}" VALUES ({",".join("?" for _ in rows[0])})', rows)
            elif name in ("dim_table_metadata", "dim_column_metadata"):
                continue
            elif name not in data:
                raise KeyError(f"table {name} is not generated")
            else:
                insert(con, name, data[name])
        META.fix_catalog(con)
        META.write(con, base)
        con.commit()
        checks = CHK.run(con)
        failed = {k: v for k, v in checks.items() if not v["pass"]}
        if failed:
            raise RuntimeError("generated demo database failed checks: " + json.dumps(failed, ensure_ascii=False, default=str)[:4000])
        con.execute("VACUUM")
        con.close()
        con = None
        if output.exists():
            output.unlink()
        os.replace(tmp, output)
        out = {"ok": True, "generator_version": GENERATOR_VERSION, "seed": seed, "tables": len(tables),
               "rows": {n: len(data.get(n, [])) for n in tables if n in data}, "checks": checks,
               "database_sha256": sha256_file(output), "layout_sha256": sha256_file(layout), "window": [MASTER.START.isoformat(), MASTER.END.isoformat()]}
        out["total_rows"] = sum(out["rows"].values())
        report.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        return out
    except Exception:
        if con is not None:
            con.close()
        if tmp.exists():
            if os.environ.get("DEMO108_KEEP_FAILED"):
                print(f"kept failed build: {tmp}", file=sys.stderr)
            else:
                tmp.unlink()
        raise


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--layout", type=Path, required=True, help="original 108-table database (read only; schema and metric catalog)")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--report", type=Path, required=True)
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument("--force", action="store_true")
    a = p.parse_args()
    r = build(a.layout, a.output, a.report, a.force, a.seed)
    print(json.dumps({k: r[k] for k in ("ok", "tables", "total_rows", "database_sha256")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
