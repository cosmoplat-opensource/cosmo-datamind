#!/usr/bin/env python3
"""Protocol-v4 statistics. Per setting (R, O, MG), both folds pooled (each question is in exactly one test fold);
accuracy per question = mean over the three repeats; reported as % of questions.
Intervals: 95% bootstrap, 10,000 resamples. R and O: database-cluster bootstrap (11 databases).
MG: one schema, so questions are resampled within source database (stratified), i.e. question-level.
Paired differences use the same resamples. Ontology tool use = share of question-runs with >=1 semantic tool call.
usage: python3 stats_v4.py  -> runs/v4/stats_v4.json and runs/v4/统计_v4.md
"""
import glob
import json
import random
from collections import defaultdict
from pathlib import Path

EXP = Path(__file__).resolve().parent
V4 = EXP / "runs/v4"
CONDS = ["none", "doc", "evo", "evodoc", "dm", "dmdoc", "M"]
NAMES = {"none": "ReAct, no ontology", "doc": "+ documentation", "evo": "+ EvoOntology", "evodoc": "+ EvoOntology + doc",
         "dm": "+ DataMind ontology", "dmdoc": "+ DataMind ontology + doc", "M": "M (compiled DataMind ontology + check, with doc)"}
PAIRS = [("doc", "none"), ("evo", "none"), ("dm", "none"), ("evo", "dm"), ("evodoc", "doc"), ("dmdoc", "doc"),
         ("M", "doc"), ("M", "none"), ("M", "evodoc")]
B = 10000


def qdb(S, fold):
    sr = {"fwd": "minidev", "rev": "minidev_rev"}[fold]
    if S == "R":
        root = EXP / "EvoOntology/benchmarks/bird/data" / sr / "test"
    else:
        root = EXP / f"variants/{'opaque' if S == 'O' else 'opaque_merged'}/questions/{sr}/test"
    out = {}
    for f in root.glob("*.json"):
        for q in json.loads(f.read_text())["questions"]:
            # merged schema: stratify by source database (kept in the question record or the original db name)
            out[str(q["question_id"])] = q.get("source_db") or q.get("orig_db_id") or q["db_id"]
    return out


def tool_use(run_dir):
    f = sorted(glob.glob(str(run_dir / "*/*/all_results.json")))[-1]
    r = json.loads(Path(f).read_text())["results"]
    return sum(1 for x in r if float(x.get("semantic_tool_calls") or 0) > 0), len(r)


def load(S):
    acc = {c: {} for c in CONDS}   # cond -> qid(fold-prefixed) -> [strict, tolerant] means
    db = {}
    tu = {c: [0, 0] for c in CONDS}
    for fold in ("fwd", "rev"):
        m = qdb(S, fold)
        for c in CONDS:
            per = defaultdict(list)
            for rep in (1, 2, 3):
                d = V4 / "qa" / f"{S}_{fold}" / f"{c}_r{rep}"
                r = json.loads((d / "rescore.json").read_text())
                for i, v in r.items():
                    per[i].append(v)
                a, n = tool_use(d)
                tu[c][0] += a; tu[c][1] += n
            for i, vs in per.items():
                k = f"{fold}:{i}"
                acc[c][k] = [sum(v[0] for v in vs) / len(vs), sum(v[1] for v in vs) / len(vs)]
                db[k] = m.get(i, "?")
    return acc, db, tu


def boot(acc, db, cluster, seed=20261107):
    rnd = random.Random(seed)
    groups = defaultdict(list)
    for k, d in db.items():
        groups[d].append(k)
    keys = sorted(groups)
    samples = []
    for _ in range(B):
        if cluster:
            ks = [k for g in (rnd.choice(keys) for _ in keys) for k in groups[g]]
        else:
            ks = [k for g in keys for k in (rnd.choice(groups[g]) for _ in groups[g])]
        samples.append(ks)
    return samples


def summarize(S, cluster):
    acc, db, tu = load(S)
    samples = boot(acc, db, cluster)
    keys = list(db)
    res = {"n_questions": len(keys), "conditions": {}, "pairs": {}}

    def mean(c, ks, j):
        return 100 * sum(acc[c][k][j] for k in ks) / len(ks)

    def ci(vals):
        vals = sorted(vals)
        return [round(vals[int(0.025 * B)], 1), round(vals[int(0.975 * B) - 1], 1)]

    for c in CONDS:
        res["conditions"][c] = {
            "strict": round(mean(c, keys, 0), 1), "tolerant": round(mean(c, keys, 1), 1),
            "tolerant_ci": ci([mean(c, ks, 1) for ks in samples]),
            "tool_use": round(100 * tu[c][0] / tu[c][1], 1) if c.startswith(("evo", "dm")) else None}
    for a, b in PAIRS:
        for j, nm in ((0, "strict"), (1, "tolerant")):
            d = mean(a, keys, j) - mean(b, keys, j)
            res["pairs"][f"{a}-{b}:{nm}"] = {"diff": round(d, 1), "ci": ci([mean(a, ks, j) - mean(b, ks, j) for ks in samples])}
    return res


def main():
    out = {"R": summarize("R", True), "O": summarize("O", True), "MG": summarize("MG", False)}
    (V4 / "stats_v4.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    L = ["# Protocol v4 test results (both folds pooled, 3 repeats averaged per question)", "",
         "Column-tolerant EX (strict EX in parentheses), % of questions, 95% bootstrap CI. R/O: database-cluster; MG: question-level, stratified by source database.", "",
         "| Condition | R readable (n=%d) | O opaque (n=%d) | MG merged 60 tables (n=%d) | tool use R/O/MG |" % (out["R"]["n_questions"], out["O"]["n_questions"], out["MG"]["n_questions"]),
         "|---|---|---|---|---|"]
    for c in CONDS:
        cells = []
        for S in ("R", "O", "MG"):
            x = out[S]["conditions"][c]
            cells.append(f"{x['tolerant']} [{x['tolerant_ci'][0]}, {x['tolerant_ci'][1]}] ({x['strict']})")
        t = "/".join("—" if out[S]["conditions"][c]["tool_use"] is None else str(out[S]["conditions"][c]["tool_use"]) for S in ("R", "O", "MG"))
        L.append(f"| {NAMES[c]} | " + " | ".join(cells) + f" | {t} |")
    L += ["", "## Paired differences (points)", "", "| Pair | R tolerant | O tolerant | MG tolerant | R strict | O strict | MG strict |", "|---|---|---|---|---|---|---|"]
    for a, b in PAIRS:
        row = []
        for nm in ("tolerant", "strict"):
            for S in ("R", "O", "MG"):
                p = out[S]["pairs"][f"{a}-{b}:{nm}"]
                row.append(f"{p['diff']:+.1f} [{p['ci'][0]:+.1f}, {p['ci'][1]:+.1f}]")
        L.append(f"| {a} − {b} | " + " | ".join(row) + " |")
    (V4 / "统计_v4.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
