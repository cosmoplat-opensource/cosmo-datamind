"""A6: rule-only baseline on the v30 bank: every component is the argmax of its common prior, no learning.

field  = argmax(public field prior + v29 field prior)
period = argmax(public period prior + v27 structural prior), restricted to the chosen field's period_type when known
dims   = argmax(public dims prior + v27 structural prior)
unit_quantity = argmax(public unit prior + v28 pair prior row of the chosen field)
Scored with the frozen development references exactly like the learners."""

import json
from collections import Counter
import numpy as np
import torch
from native_binding_features_v17 import CHANNELS
from native_v30_data import load_data
from native_v30_bank import DEST as BANK
from score_native_binding_v17 import component_correct, scoring_references
from prepare import save

ENTITY_SCHEME = "http://standards.iso.org/iso/17442"


def main():
    tasks, inventory, packs, _, provenance = load_data()
    future = {t["task_id"]: t for t in tasks if t["source_year"] != 2023}
    refs, _ = scoring_references(future)
    rows, per_company = {}, Counter()
    for i, (task, item) in enumerate(zip(tasks, inventory)):
        if task["source_year"] == 2023:
            continue
        pack = packs[i]
        groups = item["components"]
        # field: node-level prior (one node per field group)
        f = int(torch.argmax(pack["prior"][0]))
        field = groups["field"][f]["value"]
        # period / dims: group score = max over witness nodes of node prior
        chosen = {"field": field}
        for j, c in enumerate(CHANNELS[1:], start=1):
            node_prior = pack["prior"][j].clone()
            if c == "unit_quantity":
                node_prior = node_prior + pack["pair_prior"][2][f][pack["groups"][j - 1]]
            gid = pack["groups"][j - 1]
            best = {}
            for n, g in enumerate(gid.tolist()):
                v = float(node_prior[n])
                if g not in best or v > best[g]:
                    best[g] = v
            g = max(best, key=best.get)
            chosen[c] = groups[c][g]["value"]
        pred = {"field": field, "entity": {"scheme": ENTITY_SCHEME, "identifier": task["company"]},
                "period": chosen["period"], "dimensions": chosen["dimensions"],
                "unit": (chosen["unit_quantity"] or {}).get("unit"), "quantity": (chosen["unit_quantity"] or {}).get("quantity")}
        rows[task["task_id"]] = component_correct(pred, refs[task["task_id"]])
        per_company[task["company"]] += rows[task["task_id"]]["semantic_tuple_exact"]
    n = len(rows)
    summary = {c: sum(r[c] for r in rows.values()) / n for c in next(iter(rows.values()))}
    # company-cluster bootstrap for exact
    rng = np.random.default_rng(0); comps = sorted(per_company); vals = {c: [rows[k]["semantic_tuple_exact"] for k in rows if future[k]["company"] == c] for c in comps}
    boots = [np.mean([v for i in rng.choice(len(comps), len(comps), replace=True) for v in vals[comps[i]]]) for _ in range(2000)]
    result = {"targets": n, "accuracy": summary, "exact_ci95_company_cluster": [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))],
              "per_company_exact": dict(per_company), "provenance": provenance, "no_learning": True}
    save(BANK / "rule_only_baseline_v30.json", result)
    print(json.dumps({k: (round(v, 4) if isinstance(v, float) else v) for k, v in summary.items()}), result["exact_ci95_company_cluster"])


if __name__ == "__main__":
    main()
