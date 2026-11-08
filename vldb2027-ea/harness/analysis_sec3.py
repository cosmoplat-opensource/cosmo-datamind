#!/usr/bin/env python3
"""Numbers of paper Section 3.4 and 3.6 not produced by stats_v3.py.
1. Industrial databases, native comparison (DataMind chain with dm-w vs ReAct with evo-w), set-rule strict: overall gap,
   contribution of each question type, and for each type the ReAct agent's wrong answers (three runs) that are correct
   under the column-tolerant rule.
2. BIRD nofk native comparison after the variant-index fix (runs/v3 'fix' units vs the v2 ReAct units), column-tolerant,
   with a 95% database-cluster bootstrap interval (10,000 resamples, seed 20261107).
3. BIRD readable schema, information-matched: DataMind chain without exemplars and documentation (runs/v3 'noexdict')
   vs the ReAct agent with EvoOntology (v2), same interval.
Writes runs/v4/analysis_sec3.json."""
import collections, glob, json, random
from pathlib import Path

EXP = Path(__file__).resolve().parent
D = EXP / "EvoOntology/benchmarks/bird/data"
R2, R3 = EXP / "runs/v2/rescore", json.load(open(EXP / "runs/v3/rescore_v3.json"))


def questions(splits):
    out = {}
    for sr, tag in splits:
        for f in glob.glob(str(D / sr / "test/*.json")):
            for q in json.load(open(f))["questions"]:
                out[(tag, str(q["question_id"]))] = q
    return out


def industrial():
    qs = questions([("ind_fwd", "ind_fwd"), ("ind_rev", "ind_rev")])
    def load(o, qq):
        out = {}
        for d in ("ind_fwd", "ind_rev"):
            for rep in (1, 2, 3):
                for k, v in json.load(open(R2 / f"{d}__{o}__{qq}__full_r{rep}.json")).items():
                    out.setdefault((d, k), []).append(v)
        return out
    dm, ev = load("dm-w", "dm"), load("evo-w", "react")
    n = len(dm)
    mean = lambda xs, j: sum(x[j] for x in xs) / len(xs)
    gap = 100 * sum(mean(dm[k], 0) - mean(ev[k], 0) for k in dm) / n
    by, wrong, tol = collections.defaultdict(float), collections.Counter(), collections.Counter()
    for k in dm:
        t = qs[k]["difficulty"]
        by[t] += 100 * (mean(dm[k], 0) - mean(ev[k], 0)) / n
        for v in ev[k]:
            if not v[0]:
                wrong[t] += 1; tol[t] += v[1]
    return {"questions": n, "strict_gap_set_rule": round(gap, 2),
            "by_type": {t: {"gap_points": round(g, 2), "react_wrong_runs": wrong[t], "tolerant_correct": tol[t]}
                        for t, g in sorted(by.items(), key=lambda x: -x[1])}}


def native(dm_key, ev_key, splits=(("minidev", "fwd"), ("minidev_rev", "rev"))):
    qs = questions(list(splits))
    db = {i: q["db_id"] for (_, i), q in qs.items()}
    out = {}
    for j, name in ((0, "strict"), (1, "tolerant")):
        def pool(get):
            reps = []
            for rep in (1, 2, 3):
                m = {}
                for d in ("fwd", "rev"):
                    m.update(get(d, rep))
                reps.append(m)
            return {i: sum(r[i] for r in reps) / 3 for i in reps[0]}
        dm = pool(lambda d, rep: {k: v[j] for k, v in R3[dm_key.format(d=d, rep=rep)].items()})
        ev = pool(lambda d, rep: {k: v[j] for k, v in json.load(open(R2 / ev_key.format(d=d, rep=rep))).items()})
        g = {}
        for i in dm:
            g.setdefault(db[i], []).append(dm[i] - ev[i])
        keys, rnd, vals = sorted(g), random.Random(20261107), []
        for _ in range(10000):
            smp = [x for k in (rnd.choice(keys) for _ in keys) for x in g[k]]
            vals.append(100 * sum(smp) / len(smp))
        vals.sort()
        allv = [x for k in keys for x in g[k]]
        out[name] = {"diff": round(100 * sum(allv) / len(allv), 1), "ci": [round(vals[250], 1), round(vals[9749], 1)],
                     "dm": round(100 * sum(dm.values()) / len(dm), 1), "evo": round(100 * sum(ev.values()) / len(ev), 1)}
    return out


def nofk_after_fix():
    qs = questions([("minidev", "fwd"), ("minidev_rev", "rev")])
    db = {i: q["db_id"] for (_, i), q in qs.items()}
    def pool(get):
        reps = []
        for rep in (1, 2, 3):
            m = {}
            for d in ("fwd", "rev"):
                m.update(get(d, rep))
            reps.append(m)
        return {i: sum(r[i] for r in reps) / 3 for i in reps[0]}
    out = {}
    for j, name in ((0, "strict"), (1, "tolerant")):
        dm = pool(lambda d, rep: {k: v[j] for k, v in R3[f"{d}__dm-w__dm__nofk__fix_r{rep}"].items()})
        ev = pool(lambda d, rep: {k: v[j] for k, v in json.load(open(R2 / f"{d}__evo-w__react__nofk_r{rep}.json")).items()})
        g = {}
        for i in dm:
            g.setdefault(db[i], []).append(dm[i] - ev[i])
        keys, rnd, vals = sorted(g), random.Random(20261107), []
        for _ in range(10000):
            s = [x for k in (rnd.choice(keys) for _ in keys) for x in g[k]]
            vals.append(100 * sum(s) / len(s))
        vals.sort()
        allv = [x for k in keys for x in g[k]]
        out[name] = {"diff": round(100 * sum(allv) / len(allv), 1), "ci": [round(vals[250], 1), round(vals[9749], 1)],
                     "dm": round(100 * sum(dm.values()) / len(dm), 1), "evo": round(100 * sum(ev.values()) / len(ev), 1)}
    return out


if __name__ == "__main__":
    res = {"industrial_extra_column": industrial(), "bird_nofk_native_after_fix": nofk_after_fix(),
           "bird_full_info_matched": native("{d}__dm-w__dm__full__noexdict_r{rep}", "{d}__evo-w__react__full_r{rep}.json")}
    (EXP / "runs/v4/analysis_sec3.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
    ind = res["industrial_extra_column"]; top = next(iter(ind["by_type"].items()))
    print("industrial gap", ind["strict_gap_set_rule"], "top type", top)
    print("nofk after fix", res["bird_nofk_native_after_fix"])
    print("full, information-matched", res["bird_full_info_matched"])
