#!/usr/bin/env python3
"""Audit a genuinely built acceptance graph on a deployed service.

This script never constructs a graph or calls a model. It reads the UI-created
IR, independently replays claimed relationship evidence against a read-only
SQLite snapshot, checks live graph/quality/CQ responses, and optionally verifies
the UI's one recorded QA response. Only --readjudicate-metrics writes to the
service, and only when the graph's scenario name has an acceptance prefix.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
import time
from urllib.parse import urlsplit

import requests  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def qi(value):
    return '"' + str(value).replace('"', '""') + '"'


def relations(ir):
    if ir.get("relations"):
        return ir["relations"], "source_concept", "target_concept"
    return ir.get("links") or [], "source", "target"


def objects_by_key(ir):
    out = {}
    for obj in ir.get("objects") or []:
        for key in (obj.get("id"), obj.get("name")):
            if key:
                out[key] = obj
    return out


def table_of(obj):
    return obj.get("table") or next(iter(obj.get("tables") or []), None)


def columns(key):
    if not isinstance(key, str):
        raise AssertionError("Join key is not a string")
    result = tuple(x.strip() for x in key.split(","))
    if not all(result) or len(set(x.casefold() for x in result)) != len(result):
        raise AssertionError("Join key contains blank or duplicate components")
    return result


def checked_columns(con, table, cols):
    known = {row[1].casefold() for row in con.execute(f"PRAGMA table_xinfo({qi(table)})")}
    if not known or any(col.casefold() not in known for col in cols):
        raise AssertionError("Relationship references a missing table or column")


def tuple_values(table, cols, alias):
    refs = [f"{alias}.{qi(col)}" for col in cols]
    condition = " AND ".join(f"{ref} IS NOT NULL AND {ref} != ''" for ref in refs)
    return f"SELECT DISTINCT {', '.join(refs)} FROM {qi(table)} AS {alias} WHERE {condition}"


def independently_measure(con, child, ck, parent, pk):
    """Use a SQL semi-join, independently of the production INTERSECT primitive."""
    checked_columns(con, child, ck)
    checked_columns(con, parent, pk)
    if len(ck) != len(pk):
        raise AssertionError("Join-key widths differ")
    cv = tuple_values(child, ck, "src")
    pv = tuple_values(parent, pk, "dst")
    child_count = con.execute(f"SELECT COUNT(*) FROM ({cv})").fetchone()[0]
    parent_count = con.execute(f"SELECT COUNT(*) FROM ({pv})").fetchone()[0]
    parent_rows = con.execute(f"SELECT COUNT(*) FROM {qi(parent)}").fetchone()[0]
    on = " AND ".join(f"p.{qi(b)} = c.{qi(a)}" for a, b in zip(ck, pk, strict=True))
    matched = con.execute(
        f"SELECT COUNT(*) FROM ({cv}) AS c WHERE EXISTS "
        f"(SELECT 1 FROM {qi(parent)} AS p WHERE {on})"
    ).fetchone()[0]
    return {"child_distinct": child_count, "parent_distinct": parent_count,
            "matched_distinct": matched, "parent_unique": parent_rows > 0 and parent_rows == parent_count,
            "overlap": 100.0 * matched / child_count if child_count else -1.0}


def declared_fk_ids(con, child, ck, parent, pk):
    groups = {}
    for row in con.execute(f"PRAGMA foreign_key_list({qi(child)})"):
        groups.setdefault(row[0], []).append(row)
    matches = []
    for ident, rows in groups.items():
        rows.sort(key=lambda row: row[1])
        target = rows[0][2]
        source_cols = tuple(row[3] for row in rows)
        target_cols = tuple(row[4] for row in rows)
        if all(col is None for col in target_cols):
            info = list(con.execute(f"PRAGMA table_info({qi(target)})"))
            target_cols = tuple(row[1] for row in sorted(info, key=lambda row: row[5]) if row[5])
        lower = lambda values: tuple(str(x).casefold() for x in values)
        if target.casefold() == parent.casefold() and lower(source_cols) == lower(ck) and lower(target_cols) == lower(pk):
            matches.append(ident)
    return matches


def audit_relations(con, ir):
    objs = objects_by_key(ir)
    rels, sk, tk = relations(ir)
    checked, issues, rows = 0, [], []
    fk_violations = {}
    for index, rel in enumerate(rels):
        if rel.get("status") != "verified":
            continue
        checked += 1
        label = f"{rel.get(sk)}→{rel.get(tk)}"
        record = {"index": index, "relation": label}
        try:
            child, parent = table_of(objs[rel[sk]]), table_of(objs[rel[tk]])
            if not child or not parent:
                raise AssertionError("Verified relationship has an unbound endpoint")
            ev = rel.get("evidence") or {}
            ck, pk = columns(ev.get("child_key")), columns(ev.get("parent_key"))
            record.update(child_table=child, parent_table=parent, child_columns=ck, parent_columns=pk)
            if ev.get("source") == "declared_fk" or ev.get("declared") is True:
                matching = declared_fk_ids(con, child, ck, parent, pk)
                if not matching:
                    raise AssertionError("Claimed FK does not match a real schema constraint")
                if child not in fk_violations:
                    fk_violations[child] = Counter(row[3] for row in con.execute(f"PRAGMA foreign_key_check({qi(child)})"))
                violations = sum(fk_violations[child][ident] for ident in matching)
                if violations:
                    raise AssertionError(f"Verified declared FK has {violations} orphan rows")
                record.update(method="foreign_key_check", orphan_rows=violations)
            else:
                measured = independently_measure(con, child, ck, parent, pk)
                if not measured["parent_unique"] or measured["overlap"] < 60:
                    raise AssertionError("Claimed verified edge fails complete-data overlap/uniqueness")
                stored_overlap = ev.get("overlap", rel.get("overlap"))
                if not isinstance(stored_overlap, (int, float)) or not math.isclose(
                        measured["overlap"], stored_overlap, abs_tol=0.051):
                    raise AssertionError("Stored overlap differs from independent SQL replay")
                for key in ("child_distinct", "parent_distinct", "matched_distinct"):
                    if key in ev and ev[key] != measured[key]:
                        raise AssertionError(f"Stored {key} differs from independent SQL replay")
                if ev.get("name_ok") is not True or ev.get("evidence_complete") is not True:
                    raise AssertionError("Verified edge lacks complete named evidence")
                record.update(method="sql_semijoin", measured=measured)
            record["passed"] = True
        except (AssertionError, KeyError, TypeError, sqlite3.Error) as exc:
            record.update(passed=False, error=str(exc)[:240])
            issues.append(record)
        rows.append(record)
    return {"checked_verified": checked, "issue_count": len(issues), "issues": issues, "rows": rows}


def read_qa_response(path):
    text = path.read_text(encoding="utf-8")
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and "results" in obj:
            return obj
        if isinstance(obj, list):
            return next(item for item in reversed(obj) if item.get("type") == "done" and "results" in item)
    except (ValueError, StopIteration):
        pass
    events = [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]
    return next(item for item in reversed(events) if item.get("type") == "done" and "results" in item)


def replay_qa(con, response, graph):
    if response.get("anchor", {}).get("ontology", {}).get("keys") != [graph]:
        raise AssertionError("The recorded QA response used a different ontology")
    results = response.get("results") or []
    if not results:
        raise AssertionError("The real QA request produced no result")
    from srv_context import sql_is_readonly
    replayed = []
    for result in results:
        sql = result.get("sql") or ""
        if not sql_is_readonly(sql):
            raise AssertionError("Recorded QA SQL is not read-only")
        cursor = con.execute(sql)
        names = [item[0] for item in cursor.description or []]
        rows = [dict(zip(names, row, strict=True)) for row in cursor.fetchmany(501)]
        stored = (result.get("data") or {}).get("rows") or []
        if len(rows) > 500 or rows != stored:
            raise AssertionError("QA result differs from complete independent SQL replay")
        replayed.append({"title": result.get("title"), "sql": sql, "rows": len(rows), "passed": True})
    return {"queries": replayed, "model_calls_by_this_script": 0}


def make_cqs(ir):
    existing = (ir.get("build_manifest") or {}).get("cqs") or []
    cqs = list(existing[:20])
    rels, sk, tk = relations(ir)
    strong = next((rel for rel in rels if rel.get("status") in ("verified", "asserted")
                   and (rel.get("semantic_status") or rel.get("semantic")) not in ("fail", "disputed", "rejected")), None)
    if strong:
        cqs.append({"q": "验收：这两个已验证对象之间是否存在可信关系路径？",
                    "expect": [strong[sk], strong[tk]]})
    cqs.append({"q": "验收：不存在的对象是否会被错误认定为可答？", "expect": ["__acceptance_missing_object__"]})
    return cqs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--graph", required=True)
    parser.add_argument("--ir", required=True, type=Path)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--report-dir", required=True, type=Path)
    parser.add_argument("--qa-response", type=Path, help="The UI's existing JSON/SSE response; no new QA call is made")
    parser.add_argument("--readjudicate-metrics", action="store_true")
    args = parser.parse_args()
    endpoint = urlsplit(args.url)
    if endpoint.scheme not in ("http", "https") or not endpoint.hostname or endpoint.username or endpoint.password:
        parser.error("--url must be an explicit HTTP(S) service URL without credentials")
    if not re.fullmatch(r"built_[A-Za-z0-9_]+", args.graph) or args.ir.name != args.graph + ".json":
        parser.error("--ir filename must match the explicitly selected built_ graph")
    ir_bytes = args.ir.read_bytes()
    ir = json.loads(ir_bytes)
    if args.readjudicate_metrics and not str((ir.get("scenario") or {}).get("name") or "").lower().startswith(
            ("验收", "acceptance", "codex_acceptance")):
        parser.error("Metric writes are restricted to dedicated acceptance graph names")
    args.report_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.trust_env = False
    checks = []

    def save(name, value):
        (args.report_dir / f"{name}.json").write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")

    def api(method, path, **kwargs):
        response = session.request(method, args.url.rstrip("/") + path, timeout=(5, 120), **kwargs)
        response.raise_for_status()
        return response.json()

    def run(name, callback):
        started = time.monotonic()
        try:
            detail = callback()
            save(name, detail)
            checks.append({"name": name, "passed": True, "seconds": round(time.monotonic() - started, 3)})
        except Exception as exc:
            checks.append({"name": name, "passed": False, "error_type": type(exc).__name__, "error": str(exc)[:250]})
        print(f"{name}: {'PASS' if checks[-1]['passed'] else 'FAIL'}", flush=True)

    def manifest_check():
        manifest = ir.get("build_manifest") or {}
        if not manifest or not manifest.get("created_at") or not manifest.get("method"):
            raise AssertionError("A real build manifest, timestamp and method are required")
        if not (manifest.get("evidence") or {}).get("tables"):
            raise AssertionError("The build did not record table evidence")
        if (ir.get("scenario") or {}).get("query_errors"):
            raise AssertionError("Build contains failed evidence queries")
        return {"graph": args.graph, "method": manifest["method"], "created_at": manifest["created_at"],
                "evidence": manifest["evidence"], "skills": manifest.get("skills"), "cqs": manifest.get("cqs"),
                "ir_sha256": hashlib.sha256(ir_bytes).hexdigest()}

    def graph_check():
        graph = api("GET", f"/api/graph/{args.graph}")
        rels = relations(ir)[0]
        if len(graph.get("nodes") or []) != len(ir.get("objects") or []) or len(graph.get("edges") or []) != len(rels):
            raise AssertionError("Live graph object/relationship counts differ from its saved IR")
        if Counter(e.get("status") for e in graph["edges"]) != Counter(r.get("status") for r in rels):
            raise AssertionError("Live graph changed relationship evidence status")
        return graph

    cqs = make_cqs(ir)
    run("build_manifest", manifest_check)
    run("live_graph", graph_check)
    with closing(sqlite3.connect(args.db.resolve().as_uri() + "?mode=ro", uri=True)) as con:
        con.execute("BEGIN")
        deadline = time.monotonic() + 120
        con.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)

        def relationship_check():
            report = audit_relations(con, ir)
            save("relationship_replay", report)
            if report["issues"]:
                raise AssertionError(f"{report['issue_count']} claimed verified relationships failed independent SQL replay")
            return report

        run("relationship_replay", relationship_check)
        if args.qa_response:
            run("recorded_qa_replay", lambda: replay_qa(con, read_qa_response(args.qa_response), args.graph))

    def cq_check():
        import cq_check as cq_module
        live = api("POST", "/api/ont/cq", json={"graph": args.graph, "cqs": cqs})
        expected = cq_module.check_all(cqs, ir)
        if live["counts"] != expected["counts"] or live["coverage"] != expected["coverage"]:
            raise AssertionError("Live CQ results disagree with saved graph replay")
        if live["items"][-1]["verdict"] != "unanswerable":
            raise AssertionError("A nonexistent object was accepted")
        return live

    def quality_check():
        result = api("POST", "/api/build/quality", json={"graph": args.graph, "cqs": cqs})
        save("quality", result)
        if not result.get("evidence", {}).get("valid"):
            raise AssertionError("Live quality gate found invalid verified evidence")
        return result

    run("cq_replay", cq_check)
    run("quality", quality_check)
    run("build_history", lambda: api("GET", f"/api/build/history/{args.graph}"))

    def export_roundtrip():
        from rdflib import Graph
        from rdflib.compare import isomorphic
        import yaml  # type: ignore[import-untyped]
        graphs, details = [], {}
        for extension, rdf_format in (("ttl", "turtle"), ("jsonld", "json-ld"), ("owl", "xml")):
            response = session.get(args.url.rstrip("/") + f"/api/graph/{args.graph}/export.{extension}", timeout=(5, 30))
            response.raise_for_status()
            if "attachment" not in response.headers.get("Content-Disposition", ""):
                raise AssertionError("Export is missing attachment metadata")
            graph = Graph().parse(data=response.text, format=rdf_format)
            graphs.append(graph)
            details[extension] = {"triples": len(graph), "bytes": len(response.content)}
        if not len(graphs[0]) or not all(isomorphic(graphs[0], graph) for graph in graphs[1:]):
            raise AssertionError("RDF export formats disagree")
        for kind in ("ontology", "semantic_model"):
            response = session.get(args.url.rstrip("/") + "/api/export/osi", params={"graph": args.graph, "kind": kind}, timeout=(5, 30))
            response.raise_for_status()
            parsed = yaml.safe_load(response.text)
            if not isinstance(parsed, dict) or not parsed:
                raise AssertionError("Ossie YAML did not parse to a model")
            details[kind] = {"bytes": len(response.content), "keys": sorted(parsed)}
        actual = api("POST", "/api/sparql", json={"graph": args.graph, "query": "SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }"})
        if int(actual["rows"][0]["n"]) != len(graphs[0]):
            raise AssertionError("Live SPARQL count differs from exported graph")
        details["sparql_triples"] = len(graphs[0])
        return details

    run("export_roundtrip_and_sparql", export_roundtrip)
    if args.readjudicate_metrics:
        run("metric_readjudication", lambda: api("POST", "/api/metric/adjudicate", json={"graph": args.graph}))
    summary = {"graph": args.graph, "service": args.url, "checks": checks,
               "passed": sum(item["passed"] for item in checks), "failed": sum(not item["passed"] for item in checks),
               "model_calls_by_this_script": 0, "new_graphs_by_this_script": 0,
               "qa_checked": bool(args.qa_response), "metric_readjudication_requested": args.readjudicate_metrics}
    save("summary", summary)
    session.close()
    return int(summary["failed"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
