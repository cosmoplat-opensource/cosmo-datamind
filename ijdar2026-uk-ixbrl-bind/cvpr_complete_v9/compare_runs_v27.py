"""Paired per-target comparison of two frozen matched runs (e.g. v26 vs v27) on the development targets.

Both runs share seeds, policies, budgets and targets, so every configuration is
paired target by target; company-cluster bootstrap gives the interval.
Only completed companies common to both runs are used; the output states which."""

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from native_v26_bank import DEST as BANK
from score_native_binding_v17 import component_correct, scoring_references

METHODS = ("public_prior", "generic_relational", "semantic_memory", "scope_relational", "frozen_semantic_memory", "scope_geometry_zeroed")
POLICIES = ("hash_prefix", "public_information_prefix"); SEEDS = (19, 41, 73); BUDGETS = (0, 5, 10)
COMPONENTS = ("semantic_tuple_exact", "field", "period", "dimensions", "unit", "quantity")


def complete_companies(root, companies):
    return sorted(c for c in companies if all((root / p / c / m / str(s) / f"{b}.json").exists() for p in POLICIES for m in METHODS for s in SEEDS for b in BUDGETS))


def load(root, p, m, s, b, companies):
    out = {}
    for c in companies:
        for q in json.loads((root / p / c / m / str(s) / f"{b}.json").read_text())["predictions"]:
            out[q["task_id"]] = q["predicted_binding"]
    return out


def bootstrap(values_by_company, rng, n=2000):
    names = list(values_by_company)
    stats = []
    for _ in range(n):
        pick = rng.choice(len(names), len(names), replace=True)
        stats.append(np.mean([v for i in pick for v in values_by_company[names[i]]]))
    return float(np.percentile(stats, 2.5)), float(np.percentile(stats, 97.5))


def main(a_root, b_root, a_name, b_name, out):
    tasks = {t["task_id"]: t for t in json.loads((BANK / "public/tasks.json").read_text())}
    future = {k: t for k, t in tasks.items() if t["source_year"] != 2023}
    companies = sorted(set(complete_companies(a_root, {t["company"] for t in future.values()})) & set(complete_companies(b_root, {t["company"] for t in future.values()})))
    sub = {k: t for k, t in future.items() if t["company"] in companies}
    refs, _ = scoring_references(sub)
    rng = np.random.default_rng(0)
    report = {"runs": {a_name: str(a_root), b_name: str(b_root)}, "companies": companies, "targets": len(sub), "configurations": {}, "pooled": {}}
    rows = {}
    for p in POLICIES:
        for m in METHODS:
            for b in BUDGETS:
                for s in SEEDS:
                    pa, pb = load(a_root, p, m, s, b, companies), load(b_root, p, m, s, b, companies)
                    rows[p, m, b, s] = {k: (component_correct(pa[k], refs[k]), component_correct(pb[k], refs[k])) for k in sub}
    table = []
    for p in POLICIES:
        for m in METHODS:
            for b in BUDGETS:
                entry = {}
                for c in COMPONENTS:
                    diff = defaultdict(list); a_mean, b_mean = [], []
                    for s in SEEDS:
                        for k, (ra, rb) in rows[p, m, b, s].items():
                            diff[sub[k]["company"]].append(float(rb[c]) - float(ra[c])); a_mean.append(float(ra[c])); b_mean.append(float(rb[c]))
                    lo, hi = bootstrap(diff, rng)
                    entry[c] = {a_name: float(np.mean(a_mean)), b_name: float(np.mean(b_mean)), "difference": float(np.mean(b_mean) - np.mean(a_mean)), "ci95": [lo, hi]}
                report["configurations"][f"{p}/{m}/{b}"] = entry
                table.append((p, m, b, entry))
    for m in METHODS:
        entry = {}
        for c in COMPONENTS:
            diff = defaultdict(list)
            for p in POLICIES:
                for b in BUDGETS:
                    for s in SEEDS:
                        for k, (ra, rb) in rows[p, m, b, s].items():
                            diff[sub[k]["company"]].append(float(rb[c]) - float(ra[c]))
            lo, hi = bootstrap(diff, rng)
            entry[c] = {"difference": float(np.mean([v for vs in diff.values() for v in vs])), "ci95": [lo, hi]}
        report["pooled"][m] = entry
    Path(out).write_text(json.dumps(report, indent=2) + "\n")
    print("%d companies, %d targets: %s -> %s" % (len(companies), len(sub), a_name, b_name))
    print("%-26s %-22s %3s  %-18s %-18s %-18s %-18s" % ("policy", "method", "bud", "exact a/b (diff)", "field", "period", "dims"))
    for p, m, b, e in table:
        cell = lambda c: "%4.1f/%4.1f (%+4.1f)" % (100 * e[c][a_name], 100 * e[c][b_name], 100 * e[c]["difference"])
        print("%-26s %-22s %3d  %-18s %-18s %-18s %-18s" % (p, m, b, cell("semantic_tuple_exact"), cell("field"), cell("period"), cell("dimensions")))
    print("\npooled over policies/budgets/seeds, %s minus %s, cluster-bootstrap 95%% CI (pp)" % (b_name, a_name))
    for m in METHODS:
        print("%-22s" % m, "  ".join("%s %+4.1f [%+4.1f,%+4.1f]" % (c[:6], 100 * report["pooled"][m][c]["difference"], 100 * report["pooled"][m][c]["ci95"][0], 100 * report["pooled"][m][c]["ci95"][1]) for c in COMPONENTS))


if __name__ == "__main__":
    a, b, an, bn, out = sys.argv[1:6]
    main(Path(a), Path(b), an, bn, out)
