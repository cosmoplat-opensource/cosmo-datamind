#!/usr/bin/env python3
"""Paper Section 6.6: ablations of the DataMind answering chain (protocol v3), column-tolerant and strict, three repeats,
both folds. Each ablation is compared with the full chain (v2 unit dm-w__dm__full) on the same questions.
BIRD (11 databases): database-cluster bootstrap. Industrial (2 databases): questions resampled within database
(question-level; two databases cannot support a database-level interval). 10,000 resamples, seed 20261107.
Also, with exemplars and documentation removed (noexdict), the ontology effect inside the chain: dm-w and evo-w vs none.
Writes runs/v4/analysis_ablation_v3.json."""
import glob, json, random
from pathlib import Path

EXP = Path(__file__).resolve().parent
R2, R3 = EXP / "runs/v2/rescore", json.load(open(EXP / "runs/v3/rescore_v3.json"))
D = EXP / "EvoOntology/benchmarks/bird/data"
POOLS = {"BIRD": (("fwd", "minidev"), ("rev", "minidev_rev")), "IND": (("ind_fwd", "ind_fwd"), ("ind_rev", "ind_rev"))}
ABL = {"noex": "no exemplars", "nodict": "no column documentation", "noexdict": "neither", "norel": "ontology without relations",
       "nosem": "without concepts and caveats", "nolex": "without Chinese names", "nomet": "without metrics and catalogues",
       "gaterel": "SQL check: table scope only", "intent": "no intent cross-check"}


def dbmap(pool):
    m = {}
    for d, sr in POOLS[pool]:
        for f in glob.glob(str(D / sr / "test/*.json")):
            for q in json.load(open(f))["questions"]:
                m[(d, str(q["question_id"]))] = q["db_id"]
    return m


def unit(pool, get, j):
    reps = []
    for rep in (1, 2, 3):
        m = {}
        for d, _ in POOLS[pool]:
            src = get(d, rep)
            if src is None:
                return None
            m.update({(d, k): v[j] for k, v in src.items()})
        reps.append(m)
    return {k: sum(r[k] for r in reps) / 3 for k in reps[0]}


def v2(O, S="full"):
    return lambda d, rep: json.load(open(R2 / f"{d}__{O}__dm__{S}_r{rep}.json"))


def v3(O, cond, S="full"):
    return lambda d, rep: R3.get(f"{d}__{O}__dm__{S}__{cond}_r{rep}")


def diff(pool, a, b, dbs, cluster):
    g = {}
    for k in a:
        g.setdefault(dbs[k], []).append(a[k] - b[k])
    keys, rnd, vals = sorted(g), random.Random(20261107), []
    for _ in range(10000):
        if cluster:
            s = [x for kk in (rnd.choice(keys) for _ in keys) for x in g[kk]]
        else:
            s = [x for kk in keys for x in (rnd.choice(g[kk]) for _ in g[kk])]
        vals.append(100 * sum(s) / len(s))
    vals.sort()
    allv = [x for kk in keys for x in g[kk]]
    return {"diff": round(100 * sum(allv) / len(allv), 1), "ci": [round(vals[250], 1), round(vals[9749], 1)]}


def main():
    out = {}
    for pool in ("BIRD", "IND"):
        dbs, cluster = dbmap(pool), pool == "BIRD"
        res = {}
        for j, metric in ((1, "tolerant"), (0, "strict")):
            base = unit(pool, v2("dm-w"), j)
            res.setdefault("full_chain", {})[metric] = round(100 * sum(base.values()) / len(base), 1)
            for cond in ABL:
                u = unit(pool, v3("dm-w", cond), j)
                if u is None:
                    continue
                res.setdefault(cond, {"label": ABL[cond]})[metric] = dict(diff(pool, u, base, dbs, cluster), acc=round(100 * sum(u.values()) / len(u), 1))
            none = unit(pool, v3("none", "noexdict"), j)
            for O in ("dm-w", "evo-w"):
                u = unit(pool, v3(O, "noexdict"), j)
                res.setdefault(f"ontology_{O}_vs_none_noexdict", {})[metric] = dict(diff(pool, u, none, dbs, cluster),
                                                                                    acc=round(100 * sum(u.values()) / len(u), 1),
                                                                                    none=round(100 * sum(none.values()) / len(none), 1))
        out[pool] = res
        print(pool, json.dumps(res, ensure_ascii=False))
    (EXP / "runs/v4/analysis_ablation_v3.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
