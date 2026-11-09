"""Paired comparison of one LLM answer set against one frozen system run, per target.

Development: the run's predictions are pooled over policies, budgets and seeds (18 replicates per target),
each replicate paired with the single LLM answer for that target; 95% company-cluster bootstrap (14 clusters).
Held-out: the evaluation folder's predictions are pooled over budgets and seeds; per-company paired differences
are reported, and the target-level bootstrap is labelled descriptive (two clusters allow no cluster interval).

usage: compare_llm_run_v1.py dev|heldout <answers_dir> <run_predictions_or_evaluation_dir> <llm_name> <run_name> <out.json>
"""

import json
import os
import sys
from pathlib import Path

import numpy as np

from compare_runs_v27 import BUDGETS, COMPONENTS, METHODS, POLICIES, SEEDS, load
from score_native_binding_v17 import component_correct, scoring_references

HELD = Path(os.environ.get("HELDOUT_ROOT", str(Path(__file__).resolve().parents[1] / "runs/cvpr_complete_v10_20261014/native_correspondence/heldout_v1")))
EMPTY = {c: False for c in COMPONENTS}


def llm_rows(answers_dir, refs):
    out = {}
    for tid, ref in refs.items():
        f = answers_dir / (tid + ".json")
        b = json.loads(f.read_text()).get("predicted_binding") if f.exists() else None
        out[tid] = component_correct(b, ref) if b else dict(EMPTY)
    return out


def bootstrap(diffs_by_cluster, n=2000, seed=0):
    rng = np.random.default_rng(seed)
    clusters = sorted(diffs_by_cluster)
    boots = []
    for _ in range(n):
        pick = rng.choice(len(clusters), len(clusters), replace=True)
        vals = [v for i in pick for v in diffs_by_cluster[clusters[i]]]
        boots.append(float(np.mean(vals)))
    return [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))]


def main(split, answers_dir, run_dir, llm_name, run_name, out):
    if split == "dev":
        from native_v30_bank import DEST as BANK
        tasks = json.loads((BANK / "public/tasks.json").read_text())
        future = {t["task_id"]: t for t in tasks if t["source_year"] != 2023}
        refs = scoring_references(future)[0]
        company = {t["task_id"]: t["company"] for t in future.values()}
        configs = [(p, m, s, b) for p in POLICIES for m in METHODS for s in SEEDS for b in BUDGETS]
        companies = sorted({c for c in company.values()})
        loader = lambda p, m, s, b: load(run_dir, p, m, s, b, companies)
    else:
        tasks = json.loads((HELD / "bank/public/tasks.json").read_text())
        future = {t["task_id"]: t for t in tasks if t["source_year"] != 2023}
        allrefs = json.loads((HELD / "scoring_only/references.json").read_text())
        refs = {k: allrefs[k] for k in future}
        company = {t["task_id"]: t["company"] for t in future.values()}
        companies = sorted({c for c in company.values()})
        configs = [("hash_prefix", m, s, b) for m in METHODS for s in SEEDS for b in BUDGETS]

        def loader(p, m, s, b):
            rows = {}
            for c in companies:
                f = run_dir / "predictions" / c / m / str(s) / f"{b}.json"
                for q in json.loads(f.read_text())["predictions"]:
                    rows[q["task_id"]] = q["predicted_binding"]
            return rows
    llm = llm_rows(answers_dir, refs)
    llm_acc = {c: float(np.mean([llm[t][c] for t in refs])) for c in COMPONENTS}
    report = {"split": split, "llm": llm_name, "run": run_name, "targets": len(refs), "llm_accuracy": llm_acc, "methods": {}}
    clusters = companies if split == "dev" else sorted(refs)  # held-out: two companies allow no cluster interval; resample targets
    cluster_of = (lambda tid: company[tid]) if split == "dev" else (lambda tid: tid)
    for m in METHODS:
        diffs = {c: {cl: [] for cl in clusters} for c in COMPONENTS}
        acc = {c: [] for c in COMPONENTS}
        per_company = {cl: {"run": [], "llm": []} for cl in companies}
        for p, mm, s, b in configs:
            if mm != m:
                continue
            preds = loader(p, m, s, b)
            for tid, ref in refs.items():
                r = component_correct(preds[tid], ref) if preds.get(tid) else dict(EMPTY)
                for c in COMPONENTS:
                    diffs[c][cluster_of(tid)].append(float(r[c]) - float(llm[tid][c])); acc[c].append(float(r[c]))
                per_company[company[tid]]["run"].append(float(r["semantic_tuple_exact"])); per_company[company[tid]]["llm"].append(float(llm[tid]["semantic_tuple_exact"]))
        entry = {"run_accuracy": {c: float(np.mean(acc[c])) for c in COMPONENTS}, "pooled": {}, "per_company_exact": {}}
        for c in COMPONENTS:
            allv = [v for cl in clusters for v in diffs[c][cl]]
            entry["pooled"][c] = {"difference": float(np.mean(allv)), "ci95": bootstrap(diffs[c]), "interval_type": "company-cluster bootstrap" if split == "dev" else "target-level bootstrap (descriptive)"}
        for cl in companies:
            entry["per_company_exact"][cl] = {"run": float(np.mean(per_company[cl]["run"])), "llm": float(np.mean(per_company[cl]["llm"])), "difference": float(np.mean(per_company[cl]["run"]) - np.mean(per_company[cl]["llm"]))}
        report["methods"][m] = entry
        print("%-22s" % m, "  ".join("%s %+5.1f [%+5.1f,%+5.1f]" % (c[:6], 100 * entry["pooled"][c]["difference"], 100 * entry["pooled"][c]["ci95"][0], 100 * entry["pooled"][c]["ci95"][1]) for c in COMPONENTS), flush=True)
    print("LLM:", {c: round(100 * v, 1) for c, v in llm_acc.items()})
    for cl, e in report["methods"]["semantic_memory"]["per_company_exact"].items():
        print("  %s run %.1f llm %.1f diff %+.1f" % (cl[-6:], 100 * e["run"], 100 * e["llm"], 100 * e["difference"]))
    Path(out).write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4], sys.argv[5], sys.argv[6])
