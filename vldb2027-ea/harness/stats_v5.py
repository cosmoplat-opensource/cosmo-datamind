#!/usr/bin/env python3
"""Protocol v5 statistics (PVLDB revision): component ablation and turn budget on BIRD-opaque-merged (MG), and
BIRD-readable-merged (MGR), with the method of stats_v4.py for MG: per question the mean over three repeats of strict and
column-tolerant EX, both folds pooled, 95% question-level bootstrap stratified by source database (10,000 resamples,
seed 20261107). MG baselines (none, evo, doc, dm) are the protocol-v4 units; MG and MGR share their test questions, so
cross-setting differences are paired by question.
Also reports, per condition, the share of question-runs that call an ontology tool and that reach the turn budget.
usage: python3 stats_v5.py  -> runs/v5/stats_v5.json
"""
import glob
import json
from collections import defaultdict
from pathlib import Path

import stats_v4 as s4

EXP = Path(__file__).resolve().parent
V4, V5 = EXP / "runs/v4/qa", EXP / "runs/v5/qa"
UNITS = {("MG", c): V4 for c in ("none", "doc", "evo", "evodoc", "dm", "dmdoc")}
UNITS.update({("MG", c): V5 for c in ("evo_norel", "evo_nomap", "none_t60", "evo_t60")})
UNITS.update({("MGR", c): V5 for c in ("none", "doc", "evo", "evodoc", "dm", "dmdoc")})
CAP = {"none_t60": 60, "evo_t60": 60}


def load():
    acc, db, use = {}, {}, {}
    for fold in ("fwd", "rev"):
        m = s4.qdb("MG", fold)
        for (S, c), root in UNITS.items():
            per, calls, capped, n = defaultdict(list), 0, 0, 0
            for rep in (1, 2, 3):
                d = root / f"{S}_{fold}" / f"{c}_r{rep}"
                for i, v in json.loads((d / "rescore.json").read_text()).items():
                    per[i].append(v)
                res = json.loads(Path(sorted(glob.glob(str(d / "*/*/all_results.json")))[-1]).read_text())["results"]
                calls += sum(1 for x in res if float(x.get("semantic_tool_calls") or 0) > 0)
                capped += sum(1 for x in res if int(x.get("turns") or 0) >= CAP.get(c, 30))
                n += len(res)
            key = f"{S}:{c}"
            u = use.setdefault(key, [0, 0, 0]); u[0] += calls; u[1] += capped; u[2] += n
            for i, vs in per.items():
                k = f"{fold}:{i}"
                acc.setdefault(key, {})[k] = [sum(v[0] for v in vs) / len(vs), sum(v[1] for v in vs) / len(vs)]
                db[k] = m.get(i, "?")
    return acc, db, use


def main():
    acc, db, use = load()
    keys = sorted(db)
    for key, a in acc.items():
        assert set(a) == set(keys), (key, len(a), len(keys))
    samples = s4.boot(acc, db, cluster=False)
    B = len(samples)
    mean = lambda c, ks, j: 100 * sum(acc[c][k][j] for k in ks) / len(ks)
    ci = lambda vals: (lambda v: [round(v[int(0.025 * B)], 1), round(v[int(0.975 * B) - 1], 1)])(sorted(vals))
    out = {"n_questions": len(keys), "conditions": {}, "pairs": {}, "interactions": {}}
    for c in acc:
        u = use[c]
        out["conditions"][c] = {"strict": round(mean(c, keys, 0), 1), "tolerant": round(mean(c, keys, 1), 1),
                                "tolerant_ci": ci([mean(c, ks, 1) for ks in samples]),
                                "tool_use_pct": round(100 * u[0] / u[2], 1), "turn_cap_pct": round(100 * u[1] / u[2], 1),
                                "tolerant_by_fold": {f: round(mean(c, [k for k in keys if k.startswith(f + ":")], 1), 1) for f in ("fwd", "rev")}}
    pairs = [("MG:evo_norel", "MG:none"), ("MG:evo_nomap", "MG:none"), ("MG:evo", "MG:evo_norel"), ("MG:evo", "MG:evo_nomap"),
             ("MG:evo_norel", "MG:evo_nomap"), ("MG:none_t60", "MG:none"), ("MG:evo_t60", "MG:evo"), ("MG:evo_t60", "MG:none_t60"),
             ("MGR:doc", "MGR:none"), ("MGR:evo", "MGR:none"), ("MGR:dm", "MGR:none"), ("MGR:evo", "MGR:dm"),
             ("MGR:evodoc", "MGR:doc"), ("MGR:dmdoc", "MGR:doc"), ("MGR:none", "MG:none"), ("MGR:doc", "MG:doc")]
    for a, b in pairs:
        for j, nm in ((0, "strict"), (1, "tolerant")):
            out["pairs"][f"{a} - {b}:{nm}"] = {"diff": round(mean(a, keys, j) - mean(b, keys, j), 1),
                                              "ci": ci([mean(a, ks, j) - mean(b, ks, j) for ks in samples])}
    # does the gain of an ontology (or of documentation) differ between the opaque and the readable merged schema?
    for c in ("evo", "dm", "doc"):
        for j, nm in ((0, "strict"), (1, "tolerant")):
            g = lambda ks: (mean(f"MG:{c}", ks, j) - mean("MG:none", ks, j)) - (mean(f"MGR:{c}", ks, j) - mean("MGR:none", ks, j))
            out["interactions"][f"gain({c}) MG minus MGR:{nm}"] = {"diff": round(g(keys), 1), "ci": ci([g(ks) for ks in samples])}
    (EXP / "runs/v5/stats_v5.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    for c, v in out["conditions"].items():
        print(f"{c:16s} tol {v['tolerant']:5.1f} {v['tolerant_ci']} strict {v['strict']:5.1f} tools {v['tool_use_pct']:5.1f}% cap {v['turn_cap_pct']:5.1f}%")
    for k, v in list(out["pairs"].items()) + list(out["interactions"].items()):
        if k.endswith("tolerant"):
            print(f"{k:44s} {v['diff']:+6.1f} {v['ci']}")


if __name__ == "__main__":
    main()
