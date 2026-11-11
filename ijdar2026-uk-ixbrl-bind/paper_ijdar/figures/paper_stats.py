"""Numbers quoted in the manuscript that are not in the run summaries: Table 9, reachability, year ends,
zero-cell accuracy, per-company spread and seed spread; with the argument "poe", the commercial zero-shot
baselines of Section 5.3 instead.  Run from cvpr_complete_v9:

    python3 ../paper_ijdar/figures/paper_stats.py [poe]

Reads frozen run outputs only; references are read for scoring after every prediction is frozen.
"""

import collections
import glob
import json
import sys
from decimal import Decimal
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_figures as M  # noqa: E402
from compare_runs_v27 import SEEDS  # noqa: E402

R = M.R
from llm_baseline_v30 import TOP_FIELDS  # noqa: E402  the concept list the language-model baselines see


def field_rank(task):
    """Concept candidates in the order the baseline prompts list them (same key as llm_baseline_v30.candidate_lists)."""
    fields = sorted(task["field_candidates"], key=lambda c: -(0.65 * c["retrieval_scores"][0] + 0.25 * c["retrieval_scores"][1] + 0.1 * c["retrieval_scores"][2]))
    return ["{" + c["namespace"] + "}" + c["name"] for c in fields]


def table9():
    """Recall of the model's 40-concept list and accuracies restricted to the cells inside it (budget 10, seed means)."""
    roots = {"dev": R / "common_candidate_v30", "heldout": R / "heldout_v1/bank", "sealed": R / "sealed_v1/bank"}
    print("\nTable 9: split | cells | recall40 | concept sys/fewshot/annotated | exact sys/fewshot/annotated")
    for split, _ in M.SPLITS:
        d = M.SPLIT_DATA[split]; refs = d["refs"]
        tasks = {t["task_id"]: t for t in json.loads((roots[split] / "public/tasks.json").read_text())}
        inside = [t for t in refs if refs[t]["field"] in field_rank(tasks[t])[:TOP_FIELDS]]
        acc = lambda rows, c: 100 * np.mean([float(r[t][c]) for r in rows for t in inside])
        sysrows = [d["sys"]["v34"][(s, 10)] for s in SEEDS]
        print("%-8s %4d %5.1f | %5.1f %5.1f %5.1f | %5.1f %5.1f %5.1f" % (split, len(refs), 100 * len(inside) / len(refs),
              acc(sysrows, "field"), acc([d["llmc"]["fewshot"]], "field"), acc([d["llmc"]["annotated"]], "field"),
              acc(sysrows, "semantic_tuple_exact"), acc([d["llmc"]["fewshot"]], "semantic_tuple_exact"), acc([d["llmc"]["annotated"]], "semantic_tuple_exact")))
        claude = {"dev": R / "common_candidate_v30/llm_baseline_v30/poe_claude-sonnet-5.5/vision/answers",
                  "heldout": R / "heldout_v1/llm/poe_claude-sonnet-5.5/vision/answers"}.get(split)
        if claude is not None:  # Claude Sonnet 5.5 (zero-shot, page crop) on the same within-list cells; Section 5.6 / S3.6
            bound = M.llm_bindings(claude, refs)
            ok = [bool(bound.get(t)) and M.component_correct(bound[t], refs[t])["field"] for t in inside]
            print("%-8s Claude Sonnet 5.5 concept within the list: %5.1f on %d cells (system %5.1f)" % (split, 100 * np.mean(ok), len(inside), acc(sysrows, "field")))


def reachability():
    """Share of evaluation cells whose reference tuple is in the candidate lists (held-out 151/160 reproduces the scorer)."""
    pkey = lambda p: (p.get("kind"), p.get("instant"), p.get("start_date"), p.get("end_date"))
    dkey = lambda ds: tuple(sorted((x["axis"], x["member"]) for x in ds))
    print("\nReachability: split | cells | reachable | per component")
    for split in ("heldout_v1", "sealed_v1"):
        root = R / split
        tasks = [t for t in json.loads((root / "bank/public/tasks.json").read_text()) if t["source_year"] != 2023]
        refs = json.loads((root / "scoring_only/references.json").read_text())
        comp = collections.Counter(); allok = 0
        for t in tasks:
            ref = refs[t["task_id"]]; ok = {}
            ok["field"] = ref["field"] in {"{" + c["namespace"] + "}" + c["name"] for c in t["field_candidates"]}
            ok["period"] = any(pkey(c["period"]) == pkey(ref["period"]) for c in t["period_candidates"])
            ok["dimensions"] = any(dkey(c["dimensions"]) == dkey(ref["dimensions"]) for c in t["dimension_tuple_candidates"])
            ok["unit"] = any(c["unit"] == ref["unit"] for c in t["unit_candidates"])
            mults = {Decimal(str(c.get("multiplier", "1"))) for c in t["unit_candidates"]}
            q = abs(Decimal(str(ref["quantity"]))); vals = set()
            for c in t["quantity_candidates"]:
                pv = c.get("printed_value")
                if pv is None:
                    continue
                try:
                    v = Decimal(str(pv).replace(",", ""))
                except Exception:
                    continue
                vals |= {abs(v * m) for m in mults}
            ok["quantity"] = q in vals or (q == 0 and any(str(c.get("printed_value")) == "0" for c in t["quantity_candidates"]))
            for k, v in ok.items():
                comp[k] += v
            allok += all(ok.values())
        n = len(tasks)
        print("%-10s %4d %4d (%.1f%%) %s" % (split, n, allok, 100 * allok / n, {k: round(100 * v / n, 1) for k, v in comp.items()}))


def year_ends():
    idx = collections.defaultdict(set)
    for f in glob.glob(str(R.parents[2] / "ext/xbrl_scope_source_index/gb_page_*.json")):
        for r in json.loads(Path(f).read_text())["data"]:
            a = r["attributes"]; idx[a["fxo_id"].split("-")[0]].add(a["period_end"][5:])
    tasks = json.loads((R / "common_candidate_v30/public/tasks.json").read_text())
    ends = {t["company"]: t["report"] for t in tasks}  # one filing per company suffices: all three share the month-day
    held = sorted(set(M.SPLIT_DATA["heldout"]["company"].values()))
    print("\nYear ends: development", collections.Counter(sorted(idx[c])[0] for c in sorted(ends) if idx[c]))
    print("held-out", {c[-6:]: sorted(idx[c]) for c in held})
    sel = json.loads((R / "sealed_v1/selection.json").read_text())
    print("sealed", collections.Counter(v[0][5:] for v in sel["filings"].values()))


def development_extras():
    d = M.SPLIT_DATA["dev"]; refs = d["refs"]
    zero = [t for t in refs if Decimal(str(refs[t]["quantity"])) == 0]
    rows = [d["sys"]["v34"][(s, 10)] for s in SEEDS]
    acc = lambda ts: 100 * np.mean([float(r[t]["semantic_tuple_exact"]) for r in rows for t in ts])
    print("\nv34 budget 10: zero cells %d %.1f%% | non-zero %.1f%%" % (len(zero), acc(zero), acc([t for t in refs if t not in zero])))
    per = sorted(acc([t for t in refs if d["company"][t] == c]) for c in sorted(set(d["company"].values())))
    print("per-company range %.1f-%.1f median %.1f" % (per[0], per[-1], float(np.median(per))))
    for v in ("v30", "v31", "v32", "v33", "v34"):
        pc = json.loads((R / f"common_candidate_{v}/learning_v1/development_results.json").read_text())["per_configuration"]
        vals = [100 * pc[f"hash_prefix/semantic_memory/{s}/10"]["semantic_tuple_exact_accuracy"] for s in SEEDS]
        print("%s seed sd at budget 10: %.2f" % (v, float(np.std(vals, ddof=1))))


def poe_subset(k=320, n_boot=2000, only=None):
    """Commercial zero-shot baselines (Poe API) on the first k development evaluation cells in task-bank order
    (k=320 covers 8 of the 14 companies, k=560 is the full set).  v34 is the seed mean at budget 10; the interval is a
    company-cluster bootstrap seeded per baseline, so adding a baseline does not change the others' intervals.
    "unparsed" counts cells for which the model returned no usable binding (scored as wrong on every component)."""
    d = M.SPLIT_DATA["dev"]; refs = d["refs"]
    order = [t["task_id"] for t in json.loads((R / "common_candidate_v30/public/tasks.json").read_text()) if t["source_year"] != 2023]
    sub = [t for t in order[:k] if t in refs]
    base = R / "common_candidate_v30/llm_baseline_v30"
    runs = {"gpt-5.4 text": base / "poe_GPT-5.4/text/answers", "gpt-5.4 vision": base / "poe_GPT-5.4/vision/answers",
            "claude-sonnet-5.5 vision": base / "poe_claude-sonnet-5.5/vision/answers", "gemini-3.1-pro vision": base / "poe_gemini-3.1-pro/vision/answers"}
    runs = {n: p for n, p in runs.items() if only is None or n in only}
    empty = {c: False for c in M.COMPONENTS}
    score = lambda b: {t: (M.component_correct(b[t], refs[t]) if b.get(t) else dict(empty)) for t in sub}
    bindings = {name: M.llm_bindings(p, {t: refs[t] for t in sub}) for name, p in runs.items()}
    rows = {name: score(b) for name, b in bindings.items()}
    for name in ("zeroshot", "fewshot", "annotated"):
        rows["deepseek-flash " + name] = {t: d["llmc"][name][t] for t in sub}
    sysm = {t: np.mean([float(d["sys"]["v34"][(s, 10)][t]["semantic_tuple_exact"]) for s in SEEDS]) for t in sub}
    comps = sorted({d["company"][t] for t in sub})
    print("\nPoe subset: %d cells, %d companies; v34 budget 10 = %.1f" % (len(sub), len(comps), 100 * np.mean(list(sysm.values()))))
    print("baseline | unparsed | exact | concept period dims | v34 - baseline [95% company-cluster CI] | companies v34 ahead")
    for name, r in rows.items():
        unparsed = sum(1 for t in sub if not bindings[name].get(t)) if name in runs else 0
        diff = {c: [sysm[t] - float(r[t]["semantic_tuple_exact"]) for t in sub if d["company"][t] == c] for c in comps}
        rng = np.random.default_rng(0); boot = [np.mean([v for i in rng.choice(len(comps), len(comps)) for v in diff[comps[i]]]) for _ in range(n_boot)]
        ahead = sum(np.mean(diff[c]) > 0 for c in comps)
        a = lambda c: 100 * np.mean([float(r[t][c]) for t in sub])
        print("%-26s %3d | %5.1f | %5.1f %5.1f %5.1f | %+5.1f [%+5.1f, %+5.1f] | %d/%d" % (name, unparsed, a("semantic_tuple_exact"), a("field"), a("period"), a("dimensions"),
              100 * np.mean([v for c in comps for v in diff[c]]), 100 * np.percentile(boot, 2.5), 100 * np.percentile(boot, 97.5), ahead, len(comps)))


def annotation_gain(n_boot=2000):
    """Annotated minus plain few-shot model, per split: company-cluster bootstrap on development and sealed,
    target-level on the two held-out companies (as in the rest of the paper)."""
    print("\nAnnotation gain (annotated - few-shot), exact tuple and concept, 95% interval")
    for split in ("dev", "heldout", "sealed"):
        d = M.SPLIT_DATA[split]; refs = d["refs"]; comp = d["company"]
        clusters = sorted(set(comp.values())) if split != "heldout" else sorted(refs)
        key = (lambda t: comp[t]) if split != "heldout" else (lambda t: t)
        for c in ("semantic_tuple_exact", "field", "period"):
            diff = {k: [] for k in clusters}
            for t in refs:
                diff[key(t)].append(float(d["llmc"]["annotated"][t][c]) - float(d["llmc"]["fewshot"][t][c]))
            rng = np.random.default_rng(0)
            boot = [np.mean([v for i in rng.choice(len(clusters), len(clusters)) for v in diff[clusters[i]]]) for _ in range(n_boot)]
            ahead = sum(np.mean([float(d["llmc"]["annotated"][t][c]) - float(d["llmc"]["fewshot"][t][c]) for t in refs if comp[t] == k]) > 0 for k in sorted(set(comp.values())))
            print("%-8s %-21s %+5.1f [%+5.1f, %+5.1f]  companies positive %d" % (split, c, 100 * np.mean([v for k in clusters for v in diff[k]]), 100 * np.percentile(boot, 2.5), 100 * np.percentile(boot, 97.5), ahead))


if __name__ == "__main__":
    M.load_all()
    if "poe" in sys.argv[1:]:
        poe_subset(); poe_subset(560, only={"claude-sonnet-5.5 vision", "gemini-3.1-pro vision", "gpt-5.4 vision"}); sys.exit()
    annotation_gain()
    table9()
    development_extras()
    reachability()
    year_ends()
