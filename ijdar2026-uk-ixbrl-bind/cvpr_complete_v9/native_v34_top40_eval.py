"""v34 restricted at answer time to the language-model baselines' concept list (development split).

Question (manuscript Section 5.6/6): is the system's concept lead over the language-model controls only a consequence of
scoring all 512 concept candidates while the models see the 40 highest-ranked ones?  This script re-runs inference of
the frozen v34 semantic-memory composer (same parents, same local checkpoints, same priors) with every concept candidate
outside the prompt's 40-concept list (llm_baseline_v30.candidate_lists order, TOP_FIELDS = 40) given a logit of -1e4,
so the composer can only choose among the 40 concepts the models see.  The mask is applied to the evaluation cells only;
training is unchanged.  The local adaptation weights of v34 were not kept, so they are re-fitted with the frozen v34
procedure and seed (LOCAL_STEPS, same replay) and checked against the recorded weight hash.  The unknown-concept
group is kept.  With --check the mask is not applied and the predictions must equal the frozen v34 predictions bit for bit.

Output: common_candidate_v34_top40/learning_v1/predictions/<policy>/<company>/semantic_memory/<seed>/<budget>.json
usage: python3 native_v34_top40_eval.py [--check] [--company LEI]
"""

import argparse
import json
from pathlib import Path

import torch
from llm_baseline_v30 import TOP_FIELDS
from native_scope_composition_v23 import ScopeComposer
from native_scope_learning_v34 import BUDGETS, FOLDER as V34, LOCAL_STEPS, POLICIES, SEEDS, bank_for, fit, predict
from native_v34_data import load_data
from native_v30_bank import DEST as BANK30
from prepare import save, sha

OUT = BANK30.parent / "common_candidate_v34_top40" / "learning_v1"
METHOD = "semantic_memory"


def prompt_concepts(task):
    fields = sorted(task["field_candidates"], key=lambda c: -(0.65 * c["retrieval_scores"][0] + 0.25 * c["retrieval_scores"][1] + 0.1 * c["retrieval_scores"][2]))
    return {"{" + c["namespace"] + "}" + c["name"] for c in fields[:TOP_FIELDS]}


def concept_key(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return "{" + value["namespace"] + "}" + value["name"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="no mask; must reproduce frozen v34 predictions")
    ap.add_argument("--company", default=None)
    args = ap.parse_args()
    torch.set_num_threads(2)
    tasks, inventory, packs, policies, _ = load_data()
    index = {t["task_id"]: i for i, t in enumerate(tasks)}
    kept = masked = 0
    masks = {}
    if not args.check:
        for i, (task, item) in enumerate(zip(tasks, inventory, strict=True)):
            if task["source_year"] == 2023:
                continue
            allowed = prompt_concepts(task)
            mask = torch.zeros(len(item["components"]["field"]))
            for j, g in enumerate(item["components"]["field"]):
                key = concept_key(g["value"])
                if key is not None and key not in allowed:
                    mask[j] = -1e4; masked += 1
                elif key is not None:
                    kept += 1
            masks[i] = mask
        print("concept candidates kept %d, masked %d" % (kept, masked), flush=True)
    companies = sorted(policies[POLICIES[0]]) if args.company is None else [args.company]
    mismatches = 0
    refit_hash_equal = []
    for company in companies:
        global_ids = [i for i, t in enumerate(tasks) if t["source_year"] == 2023 and t["company"] != company]
        future = [i for i, t in enumerate(tasks) if t["source_year"] != 2023 and t["company"] == company]
        for seed in SEEDS:
            parent = ScopeComposer(METHOD, packs[0]["context"].numel())
            parent.load_state_dict(torch.load(V34 / "parents" / company / str(seed) / METHOD / "weights.pt", weights_only=True))
            for policy in POLICIES:
                for budget in BUDGETS:
                    local = [index[ident] for ident in policies[policy][company][str(budget)]]
                    model = ScopeComposer(METHOD, packs[0]["context"].numel())
                    model.load_state_dict(parent.state_dict())
                    if budget:
                        meta = json.loads((V34 / "local" / policy / company / METHOD / str(seed) / (str(budget) + ".json")).read_text())
                        ck = OUT / "local" / policy / company / METHOD / str(seed) / (str(budget) + ".pt")
                        if ck.exists():
                            model.load_state_dict(torch.load(ck, weights_only=True))
                        else:
                            exposure = fit(model, local, global_ids, packs, LOCAL_STEPS, seed)
                            if exposure["case_sequence_sha256"] != meta["exposure"]["case_sequence_sha256"]:
                                raise ValueError("local case sequence differs from v34")
                            ck.parent.mkdir(parents=True, exist_ok=True)
                            torch.save(model.state_dict(), ck)
                            refit_hash_equal.append(sha(ck) == meta["weights_sha256"])
                    model.eval()
                    bank = bank_for(global_ids + local, packs)
                    with torch.no_grad():
                        cache = model.memory_cache(bank)
                        preds = []
                        for i in future:
                            pack = packs[i]
                            if i in masks:
                                pack = dict(pack); pack["prior"] = [pack["prior"][0] + masks[i]] + list(pack["prior"][1:])
                            preds.append(predict(model, pack, tasks[i], inventory[i], cache))
                    frozen = json.loads((V34 / "predictions" / policy / company / METHOD / str(seed) / (str(budget) + ".json")).read_text())["predictions"]
                    if args.check:
                        same = [a["predicted_binding"] == b["predicted_binding"] for a, b in zip(preds, frozen, strict=True)]
                        mismatches += same.count(False)
                        continue
                    save(OUT / "predictions" / policy / company / METHOD / str(seed) / (str(budget) + ".json"),
                         {"company": company, "seed": seed, "method": METHOD, "policy": policy, "automatic_feedback_cases": budget,
                          "restriction": "concept candidates outside the %d-concept prompt list masked at inference" % TOP_FIELDS,
                          "predictions": preds})
        print("done", company, flush=True)
    print("local refits: %d, weight hash equal to v34: %d" % (len(refit_hash_equal), sum(refit_hash_equal)), flush=True)
    if args.check:
        print("check: %d prediction mismatches against frozen v34" % mismatches, flush=True)
        if mismatches:
            raise SystemExit(1)
    else:
        save(OUT / "design.json", {"base": "common_candidate_v34 (frozen parents and local checkpoints)", "top_fields": TOP_FIELDS,
                                   "mask_logit": -1e4, "kept_concept_groups": kept, "masked_concept_groups": masked,
                                   "source_sha256": sha(Path(__file__))})


if __name__ == "__main__":
    main()
