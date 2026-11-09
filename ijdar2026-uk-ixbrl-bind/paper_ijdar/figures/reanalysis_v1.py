"""Three re-analyses of released predictions for the IJDAR manuscript (no model calls, no new runs).

1. Holm correction over the five primary comparisons of Section 5.1 (pooled paired differences, company-cluster bootstrap).
2. Development cells that pass the glyph-consistency rule of the held-out/sealed sampler (heldout_targets_v1.py), budget 10.
3. Value-error anatomy of v34 and the deepseek-flash controls at budget 10 on the three splits.

Run from cvpr_complete_v9:  python3 ../paper_ijdar/figures/reanalysis_v1.py
Writes reanalysis_v1.json next to this file.  Reads frozen predictions, LLM answers and scoring references only.

Bootstrap conventions follow compare_llm_run_v1.py / paper_stats.py: 2,000 resamples, numpy default_rng(0) per comparison,
clusters sorted, percentile 95% interval; company clusters on development and sealed, target clusters on held-out.
Two-sided bootstrap p-value: p = 2 * min((#{boot <= 0} + 1) / (B + 1), (#{boot >= 0} + 1) / (B + 1)), capped at 1;
resolution floor 2 / 2001 = 0.0010.
"""

import collections
import json
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_figures as M  # noqa: E402
from compare_runs_v27 import BUDGETS, POLICIES, SEEDS, load  # noqa: E402

R = M.R
B = 2000
EXACT = "semantic_tuple_exact"
OUT = Path(__file__).resolve().parent / "reanalysis_v1.json"


# ---------------------------------------------------------------- bootstrap helpers
def cluster_boot(sums, counts, seed=0, n=B):
    """sums/counts: per cluster (sorted keys).  Statistic = pooled mean over resampled clusters, i.e. the mean of the
    concatenated per-target values, identical to compare_llm_run_v1.bootstrap for the same rng draws."""
    keys = sorted(sums)
    s = np.array([sums[k] for k in keys], float); c = np.array([counts[k] for k in keys], float)
    rng = np.random.default_rng(seed)
    boots = np.empty(n)
    for i in range(n):
        pick = rng.choice(len(keys), len(keys), replace=True)
        boots[i] = s[pick].sum() / c[pick].sum()
    est = s.sum() / c.sum()
    lo, hi = np.percentile(boots, 2.5), np.percentile(boots, 97.5)
    p = min(1.0, 2 * min((np.sum(boots <= 0) + 1) / (n + 1), (np.sum(boots >= 0) + 1) / (n + 1)))
    return {"difference_pp": round(100 * est, 2), "ci95_pp": [round(100 * lo, 2), round(100 * hi, 2)], "p_two_sided": round(float(p), 4),
            "share_boot_le_0": float(np.mean(boots <= 0)), "share_boot_ge_0": float(np.mean(boots >= 0)), "clusters": len(keys), "values": int(c.sum())}


def diffs_to_sums(diff_by_cluster):
    return {k: float(np.sum(v)) for k, v in diff_by_cluster.items()}, {k: len(v) for k, v in diff_by_cluster.items()}


def holm(pvals, alpha=0.05):
    order = sorted(range(len(pvals)), key=lambda i: pvals[i])
    m = len(pvals); adj = [0.0] * m; running = 0.0; reject = [False] * m; still = True
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * pvals[i]))
        adj[i] = running
        if still and pvals[i] <= alpha / (m - rank):
            reject[i] = True
        else:
            still = False
    return adj, reject


# ---------------------------------------------------------------- 1. Holm over the primary family
def run_correct(version, method, refs):
    root = R / f"common_candidate_{version}/learning_v1/predictions"
    comps = sorted({M.SPLIT_DATA["dev"]["company"][t] for t in refs})
    out = {}
    for p in POLICIES:
        for s in SEEDS:
            for b in BUDGETS:
                preds = load(root, p, method, s, b, comps)
                missing = [t for t in refs if t not in preds]
                assert not missing, (version, method, p, s, b, len(missing))
                out[p, s, b] = {t: M.component_correct(preds[t], refs[t]) for t in refs}
    return out


def pooled_run_vs_run(a, b, refs, company, comp=EXACT):
    """b minus a, pooled over the 18 (policy, seed, budget) configurations, per-company diff lists."""
    diff = collections.defaultdict(list)
    for key in a:
        for t in refs:
            diff[company[t]].append(float(b[key][t][comp]) - float(a[key][t][comp]))
    return diff


def pooled_run_vs_llm(run, llmc, refs, company, comp=EXACT):
    diff = collections.defaultdict(list)
    for key in run:
        for t in refs:
            diff[company[t]].append(float(run[key][t][comp]) - float(llmc[t][comp]))
    return diff


def part1():
    d = M.SPLIT_DATA["dev"]; refs, company = d["refs"], d["company"]
    cache = {}
    get = lambda v, m: cache.setdefault((v, m), run_correct(v, m, refs))
    res = {}
    # (i)-(iii): semantic-memory composer, consecutive inventories (Table 2)
    for tag, a, b in (("i_structural_prior_v27_minus_v26", "v26", "v27"), ("ii_llm_concept_ranking_v31_minus_v30", "v30", "v31"),
                      ("iii_llm_period_dims_v32_minus_v31", "v31", "v32")):
        res[tag] = cluster_boot(*diffs_to_sums(pooled_run_vs_run(get(a, "semantic_memory"), get(b, "semantic_memory"), refs, company)))
    # (iv) v32 (semantic memory) minus each full-set development baseline of Table 3
    v32 = get("v32", "semantic_memory")
    base = M.DEV_LLM
    llm_dirs = {"llm_zeroshot_ocr": base / "text/answers", "llm_zeroshot_crop": base / "vision/answers",
                "history_matched_control": base / "fewshot_history/deepseek_deepseek-flash/answers",
                "annotated_control": base / "fewshot_linkbase/deepseek_deepseek-flash/answers",
                "gpt54_zeroshot_crop": base / "poe_GPT-5.4/vision/answers", "gemini31pro_zeroshot_crop": base / "poe_gemini-3.1-pro/vision/answers",
                "claude_sonnet55_zeroshot_crop": base / "poe_claude-sonnet-5.5/vision/answers"}
    for name, p in llm_dirs.items():
        llmc = M.correct(M.llm_bindings(p, refs), refs, strict=False)
        r = cluster_boot(*diffs_to_sums(pooled_run_vs_llm(v32, llmc, refs, company)))
        r["baseline_exact_pct"] = round(100 * np.mean([float(llmc[t][EXACT]) for t in refs]), 2)
        res["iv_v32_minus_" + name] = r
    # rule only: no per-target predictions were saved (rule_only_baseline_v30.json holds per-company exact counts only).
    # A company-cluster bootstrap of a pooled mean needs only per-company sums, so the exact-tuple comparison is exact.
    rule = json.loads((R / "common_candidate_v30/rule_only_baseline_v30.json").read_text())
    ncomp = collections.Counter(company[t] for t in refs)
    sums = {c: sum(float(v32[k][t][EXACT]) for k in v32 for t in refs if company[t] == c) / len(v32) - rule["per_company_exact"][c] for c in ncomp}
    r = cluster_boot(sums, dict(ncomp))
    r["baseline_exact_pct"] = round(100 * rule["accuracy"][EXACT], 2)
    r["note"] = "rule-only predictions not saved; computed from per-company exact counts (exact tuple only); v32 averaged over its 18 configurations per company"
    res["iv_v32_minus_rule_only"] = r
    # (v) scope composer minus its geometry-zeroed control; primary = v32 inventory (the system at declaration time)
    scope = {}
    for v in ("v26", "v27", "v28", "v29", "v30", "v31", "v32", "v33", "v34"):
        try:
            scope[v] = cluster_boot(*diffs_to_sums(pooled_run_vs_run(get(v, "scope_geometry_zeroed"), get(v, "scope_relational"), refs, company)))
        except (AssertionError, FileNotFoundError) as e:
            scope[v] = {"not_computed": repr(e)[:200]}
        cache.pop((v, "scope_geometry_zeroed"), None); cache.pop((v, "scope_relational"), None)
    res["v_scope_minus_geometry_zeroed_v32"] = scope["v32"]

    families = {
        "main_11": [k for k in res if k != "iv_v32_minus_annotated_control"],
        "with_annotated_12": list(res),
        "five_with_iv_as_intersection": None,
    }
    out = {"comparisons": res, "scope_vs_zeroed_by_inventory": scope, "families": {}}
    for fam, keys in families.items():
        if keys is None:
            # (iv) treated as one hypothesis: Bonferroni-within over its 8 baselines (min p * 8), then Holm over five
            iv = [k for k in res if k.startswith("iv_")]
            p_iv = min(1.0, len(iv) * min(res[k]["p_two_sided"] for k in iv))
            names = ["i_structural_prior_v27_minus_v26", "ii_llm_concept_ranking_v31_minus_v30", "iii_llm_period_dims_v32_minus_v31", "iv_(any baseline, Bonferroni over 8)", "v_scope_minus_geometry_zeroed_v32"]
            ps = [res[names[0]]["p_two_sided"], res[names[1]]["p_two_sided"], res[names[2]]["p_two_sided"], p_iv, res[names[4]]["p_two_sided"]]
        else:
            names = keys; ps = [res[k]["p_two_sided"] for k in keys]
        adj, rej = holm(ps)
        out["families"][fam] = {n: {"p": round(p, 4), "holm_adjusted_p": round(a, 4), "significant_at_0.05": r} for n, p, a, r in zip(names, ps, adj, rej)}
    return out


# ---------------------------------------------------------------- 2. glyph-consistency subset of the development cells
NUM = re.compile(r"^[\(\-–—]?[£$€]?[\d,]+(\.\d+)?[\)%]?$|^[-–—=]$")  # heldout_targets_v1.NUM


def glyph_rule(refs):
    """Same test as heldout_targets_v1.main (box_numeral + consistency), on the corrected (perception_v30) OCR words
    and the development target boxes from perception_v30/localization/public_anchors.json."""
    anch = json.loads((R / "perception_v30/localization/public_anchors.json").read_text())
    pages = json.loads((R / "perception_v30/localization/public_pages.json").read_text())
    reason = {}
    for t, ref in refs.items():
        a = anch[t]; words = pages[a["page_hash"]]["words"]; x0, y0, x1, y1 = a["target_box"]
        inside = [w["text"] for w in words if w["box"][0] < x1 and w["box"][2] > x0 and w["box"][1] < y1 and w["box"][3] > y0]
        nums = [s for s in inside if NUM.match(s.replace(" ", ""))]
        if len(nums) == 0:
            reason[t] = "no_numeral"; continue
        if len(nums) > 1:
            reason[t] = "several_numerals"; continue
        printed = re.sub(r"[^\d.]", "", nums[0].replace(",", ""))
        value = abs(Decimal(str(ref["quantity"])))
        if value != 0:
            try:
                pv = Decimal(printed) if printed else None
            except InvalidOperation:
                pv = None
            if pv is None or not any(pv * Decimal(m) == value for m in ("1", "1000", "1000000", "1000000000", "0.01")):
                reason[t] = "inconsistent_numeral"; continue
        elif nums[0].replace(" ", "") not in ("-", "–", "—", "=") and printed not in ("0", "0.0"):
            reason[t] = "inconsistent_zero"; continue
        reason[t] = "pass"
    return reason


def v34_vs_controls(split, subset, comps=(EXACT,)):
    d = M.SPLIT_DATA[split]; company = d["company"]
    clusters = (lambda t: company[t]) if split != "heldout" else (lambda t: t)
    out = {"cells": len(subset), "companies": len({company[t] for t in subset})}
    for c in comps:
        sysm = {t: np.mean([float(d["sys"]["v34"][(s, 10)][t][c]) for s in SEEDS]) for t in subset}
        out.setdefault(c, {})["v34_pct"] = round(100 * np.mean(list(sysm.values())), 2)
        for name in ("fewshot", "annotated"):
            lab = {"fewshot": "history_matched", "annotated": "annotated"}[name]
            out[c][lab + "_pct"] = round(100 * np.mean([float(d["llmc"][name][t][c]) for t in subset]), 2)
            diff = collections.defaultdict(list)
            for t in subset:
                diff[clusters(t)].append(sysm[t] - float(d["llmc"][name][t][c]))
            r = cluster_boot(*diffs_to_sums(diff))
            ahead = sum(np.mean([sysm[t] - float(d["llmc"][name][t][c]) for t in subset if company[t] == k]) > 0 for k in sorted({company[t] for t in subset}))
            r["companies_v34_ahead"] = int(ahead)
            out[c]["v34_minus_" + lab] = r
    return out


def part2():
    d = M.SPLIT_DATA["dev"]; refs = d["refs"]
    reason = glyph_rule(refs)
    passing = [t for t in refs if reason[t] == "pass"]; failing = [t for t in refs if reason[t] != "pass"]
    comps = (EXACT, "field", "period", "dimensions", "unit", "quantity")
    return {"rule_counts": dict(collections.Counter(reason.values())), "zero_cells_passing": sum(Decimal(str(refs[t]["quantity"])) == 0 for t in passing),
            "full_set_check": v34_vs_controls("dev", list(refs)), "passing": v34_vs_controls("dev", passing, comps),
            "failing": v34_vs_controls("dev", failing)}


# ---------------------------------------------------------------- 3. value-error anatomy
def classify(pred_q, gold_q):
    if pred_q is None:
        return "missing"
    try:
        p = Decimal(str(pred_q)); g = Decimal(str(gold_q))
    except InvalidOperation:
        return "glyph_other"
    if p == g:
        return None
    if abs(p) == abs(g):
        return "sign_only"
    if g == 0 or p == 0:
        return "zero_dash"
    ratio = abs(p) / abs(g)
    k = ratio.adjusted()  # floor(log10)
    if ratio == Decimal(10) ** k:
        return "scale_signflip" if (p < 0) != (g < 0) else "scale"
    return "glyph_other"


def anatomy(bindings_list, refs):
    cnt = collections.Counter(); n = 0
    for bind in bindings_list:
        for t, ref in refs.items():
            b = bind.get(t)
            cls = classify(b.get("quantity") if b else None, ref["quantity"])
            n += 1
            if cls:
                cnt[cls] += 1
    wrong = sum(cnt.values())
    shares = {k: round(100 * v / wrong, 1) for k, v in sorted(cnt.items())}
    return {"cell_predictions": n, "value_wrong": wrong, "value_wrong_pct": round(100 * wrong / n, 2), "counts": dict(cnt), "shares_of_wrong_pct": shares}


def part3():
    out = {}
    for split, _ in M.SPLITS:
        d = M.SPLIT_DATA[split]; refs = d["refs"]
        out[split] = {"cells": len(refs),
                      "v34_seed_pooled": anatomy([d["sysb"]["v34"][s] for s in SEEDS], refs),
                      "v34_per_seed": {s: anatomy([d["sysb"]["v34"][s]], refs) for s in SEEDS},
                      "history_matched": anatomy([d["llmb"]["fewshot"]], refs),
                      "annotated": anatomy([d["llmb"]["annotated"]], refs)}
        ps = out[split]["v34_per_seed"]
        keys = {k for s in SEEDS for k in ps[s]["shares_of_wrong_pct"]}
        out[split]["v34_per_seed_mean_shares_pct"] = {k: round(float(np.mean([ps[s]["shares_of_wrong_pct"].get(k, 0.0) for s in SEEDS])), 1) for k in sorted(keys)}
    return out


if __name__ == "__main__":
    M.load_all()
    result = {"conventions": __doc__, "part1_holm": part1(), "part2_glyph_subset": part2(), "part3_value_errors": part3()}
    OUT.write_text(json.dumps(result, indent=1, default=str) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "conventions"}, indent=1, default=str)[:20000])
    print("wrote", OUT)
