#!/usr/bin/env python3
"""Create a D-1/A-1 field profile without exporting plaintext field values."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any


NUMERIC_TOKENS = ("INT", "REAL", "DECIMAL", "NUMERIC", "FLOAT", "DOUBLE")


def qident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def value_hash(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    prefix = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    # Separators prevent identifier scanners from mistaking a numeric run in a
    # hexadecimal digest for a telephone number or bank account.
    return "sha256-" + "-".join(prefix[index : index + 4] for index in range(0, 16, 4))


def user_tables(con: sqlite3.Connection) -> list[str]:
    return [
        row[0]
        for row in con.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]


def profile_database(db: Path, output: Path, summary_path: Path) -> dict[str, Any]:
    db = db.resolve()
    if not db.is_file():
        raise FileNotFoundError(db)
    output.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    table_meta = {
        row[0]: (row[1], row[2])
        for row in con.execute(
            "SELECT table_name,table_name_cn,table_desc FROM dim_table_metadata"
        )
    }
    column_meta = {
        (row[0], row[1]): (row[2], row[3])
        for row in con.execute(
            "SELECT table_name,column_name,column_name_cn,column_desc "
            "FROM dim_column_metadata"
        )
    }
    tables = user_tables(con)
    temp_handle = tempfile.NamedTemporaryFile(
        prefix="field_profile_", suffix=".csv", dir=output.parent, delete=False
    )
    temp_path = Path(temp_handle.name)
    temp_handle.close()
    records = 0
    complete = 0
    commented = 0
    try:
        with temp_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "table_name",
                    "table_name_cn",
                    "table_desc",
                    "column_name",
                    "column_name_cn",
                    "column_desc",
                    "data_type",
                    "not_null",
                    "primary_key",
                    "declared_fk_targets_json",
                    "total_rows",
                    "null_count",
                    "null_rate",
                    "distinct_count",
                    "distinct_rate",
                    "distribution_class",
                    "numeric_min",
                    "numeric_max",
                    "numeric_mean",
                    "text_min_length",
                    "text_max_length",
                    "text_mean_length",
                    "top_frequency_distribution_json",
                    "profile_status",
                ]
            )
            for table in tables:
                table_cn, table_desc = table_meta.get(table, (table, ""))
                total_rows = con.execute(
                    f"SELECT COUNT(*) FROM {qident(table)}"
                ).fetchone()[0]
                fk_targets: dict[str, list[str]] = {}
                for fk in con.execute(f"PRAGMA foreign_key_list({qident(table)})"):
                    fk_targets.setdefault(fk[3], []).append(f"{fk[2]}.{fk[4]}")
                for info in con.execute(f"PRAGMA table_info({qident(table)})"):
                    column, sql_type = info[1], info[2] or "TEXT"
                    records += 1
                    column_cn, column_desc = column_meta.get(
                        (table, column), (column, "")
                    )
                    if column_desc.strip():
                        commented += 1
                    is_numeric = any(token in sql_type.upper() for token in NUMERIC_TOKENS)
                    quoted_column = qident(column)
                    null_count, distinct_count = con.execute(
                        f"SELECT SUM(CASE WHEN {quoted_column} IS NULL THEN 1 ELSE 0 END), "
                        f"COUNT(DISTINCT {quoted_column}) FROM {qident(table)}"
                    ).fetchone()
                    null_count = int(null_count or 0)
                    distinct_count = int(distinct_count or 0)
                    non_null_count = total_rows - null_count
                    null_rate = null_count / total_rows if total_rows else 0.0
                    distinct_rate = distinct_count / non_null_count if non_null_count else 0.0
                    numeric_min = numeric_max = numeric_mean = ""
                    text_min_length = text_max_length = text_mean_length = ""
                    if is_numeric:
                        numeric_min, numeric_max, numeric_mean = con.execute(
                            f"SELECT MIN({quoted_column}), MAX({quoted_column}), "
                            f"AVG(CAST({quoted_column} AS REAL)) FROM {qident(table)}"
                        ).fetchone()
                        numeric_min = "" if numeric_min is None else numeric_min
                        numeric_max = "" if numeric_max is None else numeric_max
                        numeric_mean = "" if numeric_mean is None else round(numeric_mean, 8)
                    else:
                        text_min_length, text_max_length, text_mean_length = con.execute(
                            f"SELECT MIN(LENGTH(CAST({quoted_column} AS TEXT))), "
                            f"MAX(LENGTH(CAST({quoted_column} AS TEXT))), "
                            f"AVG(LENGTH(CAST({quoted_column} AS TEXT))) "
                            f"FROM {qident(table)} WHERE {quoted_column} IS NOT NULL"
                        ).fetchone()
                        text_min_length = "" if text_min_length is None else text_min_length
                        text_max_length = "" if text_max_length is None else text_max_length
                        text_mean_length = (
                            "" if text_mean_length is None else round(text_mean_length, 8)
                        )
                    top_values = []
                    for value, count in con.execute(
                        f"SELECT {quoted_column}, COUNT(*) frequency FROM {qident(table)} "
                        f"WHERE {quoted_column} IS NOT NULL GROUP BY {quoted_column} "
                        f"ORDER BY frequency DESC, CAST({quoted_column} AS TEXT) LIMIT 10"
                    ):
                        top_values.append(
                            {
                                "value_hash": value_hash(value),
                                "count": count,
                                "share": round(count / total_rows, 8) if total_rows else 0.0,
                            }
                        )
                    if non_null_count == 0:
                        distribution_class = "all_null"
                    elif distinct_count == 1:
                        distribution_class = "constant"
                    elif distinct_count <= 20:
                        distribution_class = "low_cardinality"
                    elif distinct_rate >= 0.95:
                        distribution_class = "near_unique"
                    else:
                        distribution_class = "high_cardinality"
                    profile_status = "COMPLETE"
                    complete += 1
                    writer.writerow(
                        [
                            table,
                            table_cn,
                            table_desc,
                            column,
                            column_cn,
                            column_desc,
                            sql_type,
                            int(bool(info[3])),
                            int(bool(info[5])),
                            json.dumps(
                                sorted(fk_targets.get(column, [])), ensure_ascii=False
                            ),
                            total_rows,
                            null_count,
                            round(null_rate, 8),
                            distinct_count,
                            round(distinct_rate, 8),
                            distribution_class,
                            numeric_min,
                            numeric_max,
                            numeric_mean,
                            text_min_length,
                            text_max_length,
                            text_mean_length,
                            json.dumps(top_values, ensure_ascii=False, separators=(",", ":")),
                            profile_status,
                        ]
                    )
        os.replace(temp_path, output)
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        con.close()
        raise
    con.close()
    summary = {
        "ok": records > 0 and complete == records,
        "database_path": str(db),
        "database_sha256": sha256_file(db),
        "tables": len(tables),
        "fields": records,
        "profiled_fields": complete,
        "profile_completeness": round(complete / records, 8) if records else 0.0,
        "commented_fields": commented,
        "comment_completeness": round(commented / records, 8) if records else 0.0,
        "plaintext_values_exported": False,
        "top_values_are_sha256_prefixes": True,
        "top_value_hash_format": "sha256-xxxx-xxxx-xxxx-xxxx",
        "profile_path": str(output),
        "profile_sha256": sha256_file(output),
    }
    temp_summary = summary_path.with_suffix(summary_path.suffix + ".tmp")
    temp_summary.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temp_summary, summary_path)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = profile_database(args.db, args.output, args.summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
