"""Held-out companies, step G: global parents on the 14 development companies, prediction on the held-out targets.

Design mirrors the development harness: parents (3 seeds x matched methods, PRETRAIN_STEPS) are trained
on all 280 development 2023 teachers (the held-out companies were never seen); for each held-out company
and budget b in {0, 5, 10}, a local copy sees b of that company's own 2023 teachers (hash-prefix order)
for LOCAL_STEPS with replay; the memory bank is the development teachers plus those locals. Predictions
on the 160 held-out 2024/2025 targets are scored against the frozen references once at the end.
"""

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch
from native_scope_learning_v31 import (BUDGETS, LOCAL_STEPS, METHODS, PRETRAIN_STEPS, SEEDS, TRAINED_METHODS, ScopeComposer, bank_for, fit, predict)
import os
if os.environ.get("HELDOUT_DEV_LOADER", "v31") == "v32":
    from native_v32_data import load_data as load_dev
elif os.environ.get("HELDOUT_DEV_LOADER", "v31") == "v33":
    from native_v33_data import load_data as load_dev
elif os.environ.get("HELDOUT_DEV_LOADER", "v31") == "v34":
    from native_v34_data import load_data as load_dev
elif os.environ.get("HELDOUT_DEV_LOADER", "v31") == "v34_nollm":
    from native_v34_nollm_data import load_data as load_dev
else:
    from native_v31_data import load_data as load_dev
from heldout_data_v1 import load_heldout
from heldout_priors_v1 import HELD
from score_native_binding_v17 import component_correct
from prepare import save, sha

FOLDER = HELD / os.environ.get("HELDOUT_EVAL_FOLDER", "evaluation_v1")


def main():
    torch.set_num_threads(4)
    FOLDER.mkdir(parents=True, exist_ok=True)
    dev_tasks, _, dev_packs, _, provenance = load_dev()
    held_tasks, held_inventory, held_packs = load_heldout()
    # merge packs into one index space: development first, held-out after
    offset = len(dev_tasks)
    packs = dict(dev_packs)
    for i, p in held_packs.items():
        packs[offset + i] = p
    global_ids = [i for i, t in enumerate(dev_tasks) if t["source_year"] == 2023]
    held_future = {c: [offset + i for i, t in enumerate(held_tasks) if t["source_year"] != 2023 and t["company"] == c] for c in sorted({t["company"] for t in held_tasks})}
    held_history = {c: sorted([offset + i for i, t in enumerate(held_tasks) if t["source_year"] == 2023 and t["company"] == c],
                              key=lambda i: hashlib.sha256(packs[i]["task_id"].encode()).hexdigest()) for c in held_future}
    design = {"parents": "all development 2023 teachers", "budgets": list(BUDGETS), "seeds": list(SEEDS), "methods": list(METHODS), "pretrain_steps": PRETRAIN_STEPS,
              "local_steps": LOCAL_STEPS, "policy": "hash_prefix over the held-out company's own 2023 teachers", "development_provenance": provenance}
    save(FOLDER / "design.json", design)
    design_sha = sha(FOLDER / "design.json")
    predictions = {}
    for seed in SEEDS:
        parents = {}
        for method in TRAINED_METHODS:
            torch.manual_seed(seed)
            model = ScopeComposer(method, packs[0]["context"].numel())
            weight = FOLDER / "parents" / str(seed) / (method + ".pt")
            if weight.exists():
                model.load_state_dict(torch.load(weight, weights_only=True))
            else:
                exposure = fit(model, global_ids, [], packs, PRETRAIN_STEPS, seed)
                weight.parent.mkdir(parents=True, exist_ok=True)
                torch.save(model.state_dict(), weight)
                save(weight.with_suffix(".json"), {"exposure": exposure, "design_sha256": design_sha})
                print("global parent", seed, method, round(exposure["wall_seconds"]), "s", flush=True)
            parents[method] = model
        for company, future in held_future.items():
            for budget in BUDGETS:
                local = held_history[company][:budget]
                for method in METHODS:
                    out = FOLDER / "predictions" / company / method / str(seed) / (str(budget) + ".json")
                    if out.exists():
                        predictions[(company, method, seed, budget)] = json.loads(out.read_text()); continue
                    base = {"public_prior": None, "generic_relational": "generic_relational", "semantic_memory": "semantic_memory", "scope_relational": "scope_relational",
                            "frozen_semantic_memory": "semantic_memory", "scope_geometry_zeroed": "scope_relational"}[method]
                    geometry_off = method == "scope_geometry_zeroed"
                    model = None
                    if base is not None:
                        model = ScopeComposer(base, packs[0]["context"].numel())
                        model.load_state_dict(parents[base].state_dict())
                        if budget and method != "frozen_semantic_memory":
                            fit(model, local, global_ids, packs, LOCAL_STEPS, seed)
                    bank = bank_for(global_ids + local, packs)
                    rows = []
                    with torch.no_grad():
                        if model is not None:
                            model.eval()
                        cache = model.memory_cache(bank) if model is not None else None
                        for i in future:
                            hi = i - offset
                            rows.append(predict(model, packs[i], held_tasks[hi], held_inventory[hi], cache, geometry_off=geometry_off))
                    out.parent.mkdir(parents=True, exist_ok=True)
                    record = {"company": company, "method": method, "seed": seed, "budget": budget, "local_task_ids": [packs[i]["task_id"] for i in local], "predictions": rows}
                    save(out, record); predictions[(company, method, seed, budget)] = record
                    print("predicted", company[-6:], method, seed, budget, flush=True)
    # scoring (first and only read of the held-out references)
    refs = json.loads((HELD / "scoring_only/references.json").read_text())
    results = {}
    for (company, method, seed, budget), record in predictions.items():
        rows = {p["task_id"]: component_correct(p["predicted_binding"], refs[p["task_id"]]) for p in record["predictions"]}
        results.setdefault(f"{method}/{budget}", {}).setdefault(company, []).append({"seed": seed, "n": len(rows), "exact": sum(r["semantic_tuple_exact"] for r in rows.values()),
                                                                                       "components": {c: sum(r[c] for r in rows.values()) for c in ("field", "period", "dimensions", "unit", "quantity")},
                                                                                       "per_target": {k: r["semantic_tuple_exact"] for k, r in rows.items()}})
    summary = {}
    for key, by_company in results.items():
        n = sum(v[0]["n"] for v in by_company.values())
        exact = np.mean([sum(s["exact"] for s in v) / len(v) for v in by_company.values()]) if False else sum(sum(s["exact"] for s in v) / len(v) for v in by_company.values()) / n
        comps = {c: sum(sum(s["components"][c] for s in v) / len(v) for v in by_company.values()) / n for c in ("field", "period", "dimensions", "unit", "quantity")}
        summary[key] = {"targets": n, "exact": exact, "components": comps, "per_company": {c: sum(s["exact"] for s in v) / len(v) / v[0]["n"] for c, v in by_company.items()}}
    save(FOLDER / "results.json", {"summary": summary, "detail": {k: v for k, v in ((key, by) for key, by in results.items())}, "scored_utc": datetime.now(timezone.utc).isoformat(),
                                   "design_sha256": design_sha, "held_out_companies": sorted(held_future), "references_read_only_at_scoring": True})
    for key in sorted(summary):
        s = summary[key]
        print("%-32s n=%d exact %5.1f%% | %s | %s" % (key, s["targets"], 100 * s["exact"], " ".join("%s %4.1f" % (c[:4], 100 * v) for c, v in s["components"].items()),
                                                     " ".join("%s %4.1f" % (c[-6:], 100 * v) for c, v in s["per_company"].items())), flush=True)


if __name__ == "__main__":
    main()
