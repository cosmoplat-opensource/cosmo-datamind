#!/usr/bin/env python3
"""Run five-pass acceptance tests for the contract D-1 200-table delivery."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


def qident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def user_tables(con: sqlite3.Connection) -> list[str]:
    return [
        row[0]
        for row in con.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * pct))))
    return ordered[index]


def csv_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def run_quick_build_twice(db: Path, project: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="contract_quick_build_") as temp_dir:
        outputs = [Path(temp_dir) / "run1.json", Path(temp_dir) / "run2.json"]
        durations = []
        for output in outputs:
            started = time.perf_counter()
            proc = subprocess.run(
                [
                    sys.executable,
                    str(project / "quick_build.py"),
                    str(db),
                    str(output),
                    "合同200表交付复核",
                ],
                cwd=project,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            durations.append((time.perf_counter() - started) * 1000)
            if proc.returncode:
                return {
                    "ok": False,
                    "return_code": proc.returncode,
                    "stderr": proc.stderr[-2000:],
                }
        first = json.loads(outputs[0].read_text(encoding="utf-8"))
        first_hash = sha256_file(outputs[0])
        second_hash = sha256_file(outputs[1])
        relations = first.get("relations", [])
        objects = first.get("objects", [])
        object_names = {obj.get("name") for obj in objects}
        verified = [rel for rel in relations if rel.get("status") == "verified"]
        evidence_complete = sum(
            bool((rel.get("evidence") or {}).get("source"))
            and bool((rel.get("evidence") or {}).get("child_key"))
            and bool((rel.get("evidence") or {}).get("parent_key"))
            and bool((rel.get("evidence") or {}).get("decision"))
            for rel in verified
        )
        return {
            "ok": True,
            "object_count": len(objects),
            "relation_count": len(relations),
            "verified_relation_count": len(verified),
            "candidate_relation_count": sum(
                rel.get("status") == "candidate" for rel in relations
            ),
            "declared_fk_relation_count": sum(
                (rel.get("evidence") or {}).get("source") == "declared_fk"
                for rel in relations
            ),
            "missing_endpoint_count": sum(
                rel.get("source_concept") not in object_names
                or rel.get("target_concept") not in object_names
                for rel in relations
            ),
            "verified_evidence_complete_count": evidence_complete,
            "verified_evidence_completeness": (
                round(evidence_complete / len(verified), 8) if verified else 0.0
            ),
            "deterministic_byte_equal": first_hash == second_hash,
            "run_sha256": [first_hash, second_hash],
            "duration_ms": [round(value, 3) for value in durations],
            "build_quality_result": (first.get("build_quality") or {}).get("result"),
            "blocking_issues": len(
                (first.get("build_quality") or {}).get("blocking_issues", [])
            ),
            "hard_errors": len((first.get("build_quality") or {}).get("hard_errors", [])),
        }


def run_concurrent_queries(db: Path) -> dict[str, Any]:
    queries = [
        "SELECT COUNT(*) FROM dim_column_metadata",
        "SELECT COUNT(*) FROM fact_sales_order_line",
        "SELECT status,COUNT(*) FROM fact_inventory_movement GROUP BY status",
        "SELECT SUM(amount) FROM fact_gl_entry_line",
        "SELECT COUNT(*) FROM fact_shipment_line l JOIN fact_shipment h ON l.shipment_id=h.shipment_id",
        "SELECT COUNT(*) FROM fact_inspection_result r JOIN fact_inspection_lot l ON r.inspection_lot_id=l.inspection_lot_id",
        "SELECT substr(business_date,1,7),SUM(quantity) FROM fact_work_order_material GROUP BY 1",
        "SELECT table_category,COUNT(*) FROM dim_table_metadata GROUP BY table_category",
    ]

    def execute(worker: int, iteration: int) -> float:
        query = queries[(worker + iteration) % len(queries)]
        started = time.perf_counter()
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
        try:
            list(con.execute(query))
        finally:
            con.close()
        return (time.perf_counter() - started) * 1000

    latencies: list[float] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [
            pool.submit(execute, worker, iteration)
            for worker in range(16)
            for iteration in range(20)
        ]
        for future in as_completed(futures):
            try:
                latencies.append(future.result())
            except Exception as exc:  # pragma: no cover - retained in acceptance evidence
                errors.append(repr(exc))
    return {
        "workers": 16,
        "queries": len(futures),
        "errors": len(errors),
        "error_examples": errors[:5],
        "latency_ms": {
            "mean": round(statistics.mean(latencies), 3) if latencies else 0.0,
            "median": round(statistics.median(latencies), 3) if latencies else 0.0,
            "p95": round(percentile(latencies, 0.95), 3),
            "max": round(max(latencies), 3) if latencies else 0.0,
        },
    }


def test_acceptance(
    db: Path,
    dictionary: Path,
    profile: Path,
    profile_summary_path: Path,
    validation_path: Path,
    project: Path,
) -> dict[str, Any]:
    paths = [db, dictionary, profile, profile_summary_path, validation_path]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"missing artifacts: {missing}")
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    profile_summary = json.loads(profile_summary_path.read_text(encoding="utf-8"))
    dictionary_headers, dictionary_data = csv_rows(dictionary)
    profile_headers, profile_data = csv_rows(profile)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    tables = user_tables(con)
    physical_columns = {
        (table, info[1])
        for table in tables
        for info in con.execute(f"PRAGMA table_info({qident(table)})")
    }
    dictionary_columns = {
        (row["table_name"], row["column_name"]) for row in dictionary_data
    }
    profile_columns = {
        (row["table_name"], row["column_name"]) for row in profile_data
    }
    table_metadata = {
        row[0] for row in con.execute("SELECT table_name FROM dim_table_metadata")
    }
    column_metadata = {
        (row[0], row[1])
        for row in con.execute("SELECT table_name,column_name FROM dim_column_metadata")
    }
    empty_tables = [
        table
        for table in tables
        if not con.execute(
            f"SELECT EXISTS(SELECT 1 FROM {qident(table)} LIMIT 1)"
        ).fetchone()[0]
    ]
    pk_errors = []
    tables_without_pk = []
    date_parse_failures = []
    numeric_storage_failures = []
    dws_duplicate_grains = []
    for table in tables:
        info = list(con.execute(f"PRAGMA table_info({qident(table)})"))
        primary_keys = [row[1] for row in info if row[5]]
        if not primary_keys:
            tables_without_pk.append(table)
        for primary_key in primary_keys:
            total, distinct_count, null_count = con.execute(
                f"SELECT COUNT(*),COUNT(DISTINCT {qident(primary_key)}),"
                f"SUM({qident(primary_key)} IS NULL) FROM {qident(table)}"
            ).fetchone()
            if total != distinct_count or null_count:
                pk_errors.append(
                    {
                        "table": table,
                        "column": primary_key,
                        "rows": total,
                        "distinct": distinct_count,
                        "nulls": null_count,
                    }
                )
        for column_info in info:
            column = column_info[1]
            sql_type = (column_info[2] or "").upper()
            column_name = column.lower()
            if column_name.endswith("_date") or column_name == "date":
                bad = con.execute(
                    f"SELECT COUNT(*) FROM {qident(table)} "
                    f"WHERE {qident(column)} IS NOT NULL "
                    f"AND TRIM(CAST({qident(column)} AS TEXT))<>'' "
                    f"AND date({qident(column)}) IS NULL"
                ).fetchone()[0]
                if bad:
                    date_parse_failures.append(
                        {"table": table, "column": column, "rows": bad}
                    )
            if any(
                token in sql_type
                for token in ("INT", "REAL", "NUM", "DEC", "DOUBLE", "FLOAT")
            ):
                bad = con.execute(
                    f"SELECT COUNT(*) FROM {qident(table)} "
                    f"WHERE {qident(column)} IS NOT NULL "
                    f"AND typeof({qident(column)}) NOT IN ('integer','real')"
                ).fetchone()[0]
                if bad:
                    numeric_storage_failures.append(
                        {
                            "table": table,
                            "column": column,
                            "data_type": sql_type,
                            "rows": bad,
                        }
                    )
        if table.startswith("DWS_") and table.endswith("_DAILY"):
            grain = [
                row[1]
                for row in info
                if row[1] == "date" or row[1].startswith("dim_")
            ]
            if grain:
                grouped = ",".join(qident(column) for column in grain)
                duplicates = con.execute(
                    f"SELECT COALESCE(SUM(row_count-1),0) FROM ("
                    f"SELECT COUNT(*) row_count FROM {qident(table)} "
                    f"GROUP BY {grouped} HAVING row_count>1)"
                ).fetchone()[0]
                if duplicates:
                    dws_duplicate_grains.append(
                        {"table": table, "grain": grain, "duplicate_rows": duplicates}
                    )
    integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
    quick = con.execute("PRAGMA quick_check").fetchone()[0]
    fk_violations = list(con.execute("PRAGMA foreign_key_check"))
    sqlite_internal_tables = [
        row[0]
        for row in con.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name LIKE 'sqlite_%'"
        )
    ]
    declared_fks = sum(
        len(list(con.execute(f"PRAGMA foreign_key_list({qident(table)})")))
        for table in tables
    )
    indexes = con.execute(
        "SELECT COUNT(*) FROM sqlite_master "
        "WHERE type='index' AND name NOT LIKE 'sqlite_%'"
    ).fetchone()[0]
    total_rows = sum(
        con.execute(f"SELECT COUNT(*) FROM {qident(table)}").fetchone()[0]
        for table in tables
    )
    contract_tables = [
        row[0]
        for row in con.execute(
            "SELECT table_name FROM dim_table_metadata "
            "WHERE table_level='contract_validation' ORDER BY table_name"
        )
    ]
    source_marker_errors = []
    for table in contract_tables:
        columns = {
            row[1] for row in con.execute(f"PRAGMA table_info({qident(table)})")
        }
        if "source_system" not in columns:
            source_marker_errors.append({"table": table, "reason": "missing source_system"})
            continue
        bad = con.execute(
            f"SELECT COUNT(*) FROM {qident(table)} "
            "WHERE source_system IS NULL OR source_system<>'SYNTHETIC_VALIDATION'"
        ).fetchone()[0]
        if bad:
            source_marker_errors.append({"table": table, "bad_rows": bad})
    con.close()

    pass_1_checks = {
        "d1_tables_at_least_200": len(tables) >= 200,
        "a1_fields_at_least_5000": len(physical_columns) >= 5000,
        "field_comment_completeness_at_least_98pct": profile_summary.get(
            "comment_completeness", 0
        )
        >= 0.98,
        "field_profile_completeness_at_least_98pct": profile_summary.get(
            "profile_completeness", 0
        )
        >= 0.98,
        "profile_exports_no_plaintext_values": profile_summary.get(
            "plaintext_values_exported"
        )
        is False,
        "database_plaintext_scan_zero": sum(
            validation.get("business_plaintext_scan", {}).values()
        )
        == 0,
    }
    pass_2_checks = {
        "integrity_check_ok": integrity == "ok",
        "quick_check_ok": quick == "ok",
        "foreign_key_violations_zero": len(fk_violations) == 0,
        "primary_key_errors_zero": len(pk_errors) == 0,
        "date_parse_failures_zero": len(date_parse_failures) == 0,
        "numeric_storage_failures_zero": len(numeric_storage_failures) == 0,
        "dws_duplicate_grains_zero": len(dws_duplicate_grains) == 0,
        "empty_tables_zero": len(empty_tables) == 0,
        "table_metadata_exact": set(tables) == table_metadata,
        "column_metadata_exact": physical_columns == column_metadata,
        "dictionary_exact": physical_columns == dictionary_columns,
        "profile_exact": physical_columns == profile_columns,
        "profile_contains_no_raw_value_column": not any(
            header.lower() in {"value", "raw_value", "sample_value", "top_value"}
            for header in profile_headers
        ),
        "sqlite_internal_tables_absent": len(sqlite_internal_tables) == 0,
        "generator_validation_ok": validation.get("ok") is True,
    }
    logic_checks = validation.get("logical_checks", {})
    pass_3_checks = {
        "business_rule_violations_zero": sum(logic_checks.values()) == 0,
        "business_plaintext_scan_zero": sum(
            validation.get("business_plaintext_scan", {}).values()
        )
        == 0,
        "all_new_tables_have_synthetic_source_marker": len(source_marker_errors) == 0,
        "base_foreign_key_repairs_recorded": sum(
            validation.get("base_fk_repairs", {}).values()
        )
        == 7841,
        "base_temporal_repairs_recorded": sum(
            validation.get("base_temporal_repairs", {}).values()
        )
        == 60,
        "aggregate_grain_repairs_recorded": validation.get(
            "aggregate_grain_repairs", {}
        )
        == {
            "equipment_duplicate_rows_removed": 640,
            "safety_rows_regrained": 500,
            "dws_unique_grain_indexes": 15,
        },
        "empty_product_cost_table_filled": validation.get(
            "seeded_base_empty_tables", {}
        ).get("fact_product_cost")
        == 180,
    }
    quick_build = run_quick_build_twice(db, project)
    pass_4_checks = {
        "quick_build_completed": quick_build.get("ok") is True,
        "object_scale_supports_a2": quick_build.get("object_count", 0) >= 120,
        "structurally_verified_relations_at_least_300": quick_build.get(
            "verified_relation_count", 0
        )
        >= 300,
        "relation_endpoints_complete": quick_build.get("missing_endpoint_count") == 0,
        "verified_evidence_fields_complete": quick_build.get(
            "verified_evidence_completeness"
        )
        == 1.0,
        "build_is_deterministic": quick_build.get("deterministic_byte_equal") is True,
        "blocking_issues_zero": quick_build.get("blocking_issues") == 0,
        "hard_errors_zero": quick_build.get("hard_errors") == 0,
    }
    concurrency = run_concurrent_queries(db)
    read_only_blocked = False
    ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        ro.execute("CREATE TABLE should_not_exist(id INTEGER)")
    except sqlite3.OperationalError:
        read_only_blocked = True
    finally:
        ro.close()
    with tempfile.NamedTemporaryFile(prefix="contract_restore_", suffix=".db") as handle:
        source = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        restored = sqlite3.connect(handle.name)
        source.backup(restored)
        source.close()
        restored_integrity = restored.execute("PRAGMA integrity_check").fetchone()[0]
        restored_tables = len(user_tables(restored))
        restored.close()
    pass_5_checks = {
        "read_only_write_blocked": read_only_blocked,
        "concurrent_query_errors_zero": concurrency["errors"] == 0,
        "backup_restore_integrity_ok": restored_integrity == "ok",
        "backup_restore_table_count_exact": restored_tables == len(tables),
        "artifact_hashes_available": all(sha256_file(path) for path in paths),
        "dictionary_schema_present": len(dictionary_headers) > 0,
        "profile_schema_present": len(profile_headers) > 0,
    }
    passes = {
        "pass_1_contract_traceability": {
            "checks": pass_1_checks,
            "passed": all(pass_1_checks.values()),
        },
        "pass_2_structure_and_profile": {
            "checks": pass_2_checks,
            "passed": all(pass_2_checks.values()),
            "tables_without_declared_primary_key": tables_without_pk,
            "primary_key_errors": pk_errors,
            "empty_tables": empty_tables,
            "date_parse_failures": date_parse_failures,
            "numeric_storage_failures": numeric_storage_failures,
            "dws_duplicate_grains": dws_duplicate_grains,
        },
        "pass_3_data_logic_and_deidentification": {
            "checks": pass_3_checks,
            "passed": all(pass_3_checks.values()),
            "logical_checks": logic_checks,
            "business_plaintext_scan": validation.get("business_plaintext_scan", {}),
            "deidentification": validation.get("deidentification", {}),
            "source_marker_errors": source_marker_errors,
        },
        "pass_4_ontology_construction_support": {
            "checks": pass_4_checks,
            "passed": all(pass_4_checks.values()),
            "quick_build": quick_build,
            "contract_boundary": {
                "a2_confirmed_relation_count": "requires A-3 human-labelled set; database alone cannot conclude",
                "a3_precision": "requires D-3 supplied by Party B",
                "a4_recall": "requires D-3 supplied by Party B",
                "a6_confirmer_coverage": "requires Party B workflow and human review records",
            },
        },
        "pass_5_operability_reproducibility_and_load": {
            "checks": pass_5_checks,
            "passed": all(pass_5_checks.values()),
            "concurrent_queries": concurrency,
            "quick_build_duration_ms": quick_build.get("duration_ms"),
        },
    }
    return {
        "ok": all(item["passed"] for item in passes.values()),
        "scope": "Party A D-1 delivery and A-1/A-2 technical preconditions",
        "database": {
            "path": str(db),
            "sha256": sha256_file(db),
            "bytes": db.stat().st_size,
            "tables": len(tables),
            "fields": len(physical_columns),
            "rows": total_rows,
            "declared_foreign_keys": declared_fks,
            "indexes": indexes,
        },
        "passes": passes,
        "artifact_sha256": {str(path): sha256_file(path) for path in paths},
        "conclusion": {
            "d1_delivery_ready": all(pass_1_checks.values())
            and all(pass_2_checks.values())
            and all(pass_3_checks.values()),
            "supports_a1_scale_test": pass_1_checks["d1_tables_at_least_200"]
            and pass_1_checks["a1_fields_at_least_5000"]
            and pass_1_checks["field_profile_completeness_at_least_98pct"],
            "supports_a2_scale_test": pass_4_checks["object_scale_supports_a2"]
            and pass_4_checks["structurally_verified_relations_at_least_300"],
            "party_b_acceptance_metrics_proven": False,
            "party_b_acceptance_metrics_note": (
                "A-2 confirmed relations, A-3/A-4 accuracy, A-5/A-8 and B/C/E indicators "
                "must be measured from Party B deliverables, D-3 holdout data and human review records."
            ),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--dictionary", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--profile-summary", type=Path, required=True)
    parser.add_argument("--validation", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = test_acceptance(
        args.db.resolve(),
        args.dictionary.resolve(),
        args.profile.resolve(),
        args.profile_summary.resolve(),
        args.validation.resolve(),
        args.project.resolve(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix(args.output.suffix + ".tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
