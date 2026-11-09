"""Paired comparison of two systems on the 2024/25 targets of the held-out split (160) or, with HELDOUT_ROOT, the sealed split (800).

Each side is  run:<evaluation_dir>  (predictions pooled over the given budgets and seeds, hash-prefix policy)
or            llm:<answers_dir>     (one answer per target).
Differences are B minus A per target and replicate. The interval is a target-level bootstrap (descriptive on the
held-out split, whose two companies allow no cluster interval); with more than two companies (the sealed split) a
company-cluster interval is added as ci95_company_cluster. Per-company differences are reported.

usage: compare_heldout_v1.py <A> <B> <a_name> <b_name> <out.json> [--budgets 0,5,10] [--methods m1,m2]
"""

import argparse
import json
from pathlib import Path

import numpy as np
from compare_llm_run_v1 import HELD, EMPTY, bootstrap, llm_rows
from compare_runs_v27 import BUDGETS, COMPONENTS, METHODS, SEEDS
from score_native_binding_v17 import component_correct


def side(spec, refs, companies, m, budgets):
    kind, _, path = spec.partition(":"); path = Path(path)
    if kind == "llm":
        rows = llm_rows(path, refs); return [rows]
    reps = []
    for s in SEEDS:
        for b in budgets:
            rows = {}
            for c in companies:
                f = path / "predictions" / c / m / str(s) / f"{b}.json"
                for q in json.loads(f.read_text())["predictions"]:
                    rows[q["task_id"]] = component_correct(q["predicted_binding"], refs[q["task_id"]]) if q["predicted_binding"] else dict(EMPTY)
            for t in refs:  # a target missing from the run counts as wrong, as in compare_llm_run_v1
                rows.setdefault(t, dict(EMPTY))
            reps.append(rows)
    return reps


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("a"); ap.add_argument("b"); ap.add_argument("a_name"); ap.add_argument("b_name"); ap.add_argument("out")
    ap.add_argument("--budgets", default=",".join(map(str, BUDGETS))); ap.add_argument("--methods", default=",".join(METHODS))
    args = ap.parse_args(); budgets = [int(x) for x in args.budgets.split(",")]; methods = args.methods.split(",")
    tasks = json.loads((HELD / "bank/public/tasks.json").read_text())
    future = {t["task_id"]: t for t in tasks if t["source_year"] != 2023}
    allrefs = json.loads((HELD / "scoring_only/references.json").read_text()); refs = {k: allrefs[k] for k in future}
    company = {t: future[t]["company"] for t in refs}; companies = sorted(set(company.values()))
    report = {"a": args.a_name, "b": args.b_name, "budgets": budgets, "targets": len(refs), "difference": "b minus a", "methods": {}}
    for m in methods:
        A = side(args.a, refs, companies, m, budgets); B = side(args.b, refs, companies, m, budgets)
        pairs = [(x, y) for x in A for y in B] if (len(A) == 1 or len(B) == 1) else list(zip(A, B, strict=True))
        diffs = {c: {t: [] for t in refs} for c in COMPONENTS}; acc = {"a": {c: [] for c in COMPONENTS}, "b": {c: [] for c in COMPONENTS}}
        per = {cl: {"a": [], "b": []} for cl in companies}
        for x, y in pairs:
            for t in refs:
                for c in COMPONENTS:
                    diffs[c][t].append(float(y[t][c]) - float(x[t][c])); acc["a"][c].append(float(x[t][c])); acc["b"][c].append(float(y[t][c]))
                per[company[t]]["a"].append(float(x[t]["semantic_tuple_exact"])); per[company[t]]["b"].append(float(y[t]["semantic_tuple_exact"]))
        e = {"a_accuracy": {c: float(np.mean(acc["a"][c])) for c in COMPONENTS}, "b_accuracy": {c: float(np.mean(acc["b"][c])) for c in COMPONENTS}, "pooled": {}, "per_company_exact": {}}
        for c in COMPONENTS:
            allv = [v for t in refs for v in diffs[c][t]]
            e["pooled"][c] = {"difference": float(np.mean(allv)), "ci95": bootstrap(diffs[c]), "interval_type": "target-level bootstrap over the %d targets%s" % (len(refs), " (descriptive; two companies allow no cluster interval)" if len(companies) <= 2 else "")}
            if len(companies) > 2:  # enough clusters: company-cluster bootstrap as on the development set
                by_company = {cl: [v for t in refs if company[t] == cl for v in diffs[c][t]] for cl in companies}
                e["pooled"][c]["ci95_company_cluster"] = bootstrap(by_company); e["pooled"][c]["clusters"] = len(companies)
        for cl in companies:
            e["per_company_exact"][cl] = {"a": float(np.mean(per[cl]["a"])), "b": float(np.mean(per[cl]["b"])), "difference": float(np.mean(per[cl]["b"]) - np.mean(per[cl]["a"]))}
        report["methods"][m] = e
        ci = lambda c: e["pooled"][c].get("ci95_company_cluster", e["pooled"][c]["ci95"])
        print("%-22s" % m, "a %.1f b %.1f" % (100 * e["a_accuracy"]["semantic_tuple_exact"], 100 * e["b_accuracy"]["semantic_tuple_exact"]),
              "  ".join("%s %+5.1f [%+5.1f,%+5.1f]" % (c[:6], 100 * e["pooled"][c]["difference"], 100 * ci(c)[0], 100 * ci(c)[1]) for c in COMPONENTS),
              " | " + " ".join("%s %+.1f" % (cl[-6:], 100 * v["difference"]) for cl, v in e["per_company_exact"].items()), flush=True)
    Path(args.out).write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
