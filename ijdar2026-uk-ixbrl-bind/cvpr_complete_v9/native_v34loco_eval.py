"""v34loco: the v34 system with every prior fitted leave-one-company-out (development split, semantic-memory composer).

The frozen v34 priors are fitted on the 2023 teachers of all fourteen development companies, including the evaluated one.
v34loco uses the v34 LOCO field prior (native_structural_prior_v34_loco) and the v33 LOCO structural prior, both fitted
without the evaluated company, with the pair prior v28 (rule-based, no fitting).  The composer is trained exactly as in
native_scope_learning_v34 (parent on the other thirteen companies' teachers, PRETRAIN_STEPS; local adaptation on the
company's own first b teachers, LOCAL_STEPS, with replay; same seeds, orders and budgets), but only the semantic-memory
composer reported in the manuscript is trained, so the run takes hours instead of a day.
Output: common_candidate_v34loco/learning_v1/predictions/<policy>/<company>/semantic_memory/<seed>/<budget>.json
usage: python3 native_v34loco_eval.py --company LEI
"""

import argparse
import json
from pathlib import Path

import torch
from native_scope_composition_v23 import ScopeComposer
from native_scope_learning_v34 import BUDGETS, LOCAL_STEPS, POLICIES, PRETRAIN_STEPS, SEEDS, bank_for, fit, predict
from native_v34loco_data import load_data
from native_v30_bank import DEST as BANK30
from prepare import save, sha

OUT = BANK30.parent / "common_candidate_v34loco" / "learning_v1"
METHOD = "semantic_memory"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", required=True)
    args = ap.parse_args()
    torch.set_num_threads(2)
    tasks, inventory, packs, policies, provenance = load_data()
    index = {t["task_id"]: i for i, t in enumerate(tasks)}
    company = args.company
    global_ids = [i for i, t in enumerate(tasks) if t["source_year"] == 2023 and t["company"] != company]
    future = [i for i, t in enumerate(tasks) if t["source_year"] != 2023 and t["company"] == company]
    for seed in SEEDS:
        pfile = OUT / "parents" / company / str(seed) / METHOD / "weights.pt"
        torch.manual_seed(seed)
        parent = ScopeComposer(METHOD, packs[0]["context"].numel())
        if pfile.exists():
            parent.load_state_dict(torch.load(pfile, weights_only=True))
        else:
            exposure = fit(parent, global_ids, [], packs, PRETRAIN_STEPS, seed)
            pfile.parent.mkdir(parents=True, exist_ok=True)
            torch.save(parent.state_dict(), pfile)
            save(pfile.with_name("metadata.json"), {"excluded_company": company, "weights_sha256": sha(pfile), "exposure": exposure})
        for policy in POLICIES:
            for budget in BUDGETS:
                output = OUT / "predictions" / policy / company / METHOD / str(seed) / (str(budget) + ".json")
                if output.exists():
                    continue
                local = [index[ident] for ident in policies[policy][company][str(budget)]]
                if len(local) != budget or any(packs[i]["year"] != 2023 or packs[i]["company"] != company for i in local):
                    raise ValueError("Automatic feedback crossed its source/year budget")
                model = ScopeComposer(METHOD, packs[0]["context"].numel())
                model.load_state_dict(parent.state_dict())
                exposure = None
                if budget:
                    exposure = fit(model, local, global_ids, packs, LOCAL_STEPS, seed)
                model.eval()
                bank = bank_for(global_ids + local, packs)
                with torch.no_grad():
                    cache = model.memory_cache(bank)
                    preds = [predict(model, packs[i], tasks[i], inventory[i], cache) for i in future]
                save(output, {"company": company, "seed": seed, "method": METHOD, "policy": policy, "automatic_feedback_cases": budget,
                              "local_exposure": exposure, "provenance": provenance, "predictions": preds,
                              "future_reference_files_read": False})
        print("done", company, seed, flush=True)
    save(OUT / "design.json", {"base": "native_scope_learning_v34 procedure, semantic-memory composer only",
                               "priors": "field prior v34 LOCO, structural prior v33 LOCO, pair prior v28",
                               "pretrain_steps": PRETRAIN_STEPS, "local_steps": LOCAL_STEPS, "seeds": list(SEEDS),
                               "policies": list(POLICIES), "budgets": list(BUDGETS), "source_sha256": sha(Path(__file__))})


if __name__ == "__main__":
    main()
