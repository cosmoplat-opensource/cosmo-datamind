"""Held-out companies: apply the development-fitted v33 priors (few-shot LLM readings) without refitting.

Field prior: the v33 weights (prior_v33_field) scored on the held-out v29 features plus the few-shot rank
indicators from heldout llm/fewshot_rank_v1.  Structural prior: the v33 weights (prior_v33_structural) through
native_structural_prior_v32.apply with the same few-shot answers.  Outputs land in the held-out bank as
prior_v33_field and prior_v33_structural; the predictor is run with
  HELDOUT_DEV_LOADER=v33 HELDOUT_STRUCTURAL=prior_v33_structural HELDOUT_FIELD=prior_v33_field HELDOUT_EVAL_FOLDER=evaluation_v3
"""

import json

import torch
import native_structural_prior_v27 as p27
import native_structural_prior_v29 as p29
import native_structural_prior_v31 as p31
import native_structural_prior_v32 as p32
from heldout_priors_v1 import BANK, HELD
from native_structural_prior_v33 import FIELD33, PRIOR33
from prepare import save, sha

ANSWERS = HELD / "llm/fewshot_rank_v1/answers"


def apply_field(tasks, inventory):
    fit = json.loads((FIELD33 / "manifest.json").read_text())["fit"]
    p29.BANK, p29.ALIGN, p29.V27, p29.TEACHERS = BANK, HELD, BANK / "prior_v27", HELD
    ranks = {}
    for f in ANSWERS.glob("*.json"):
        d = json.loads(f.read_text()); ranks[d["task_id"]] = d.get("field_top3") or []
    if len(ranks) != len(tasks):
        raise ValueError("few-shot ranking incomplete for the held-out targets: %d / %d" % (len(ranks), len(tasks)))
    base = p31.build_v29_features(tasks, inventory)
    prior = {}
    for t, item in zip(tasks, inventory, strict=True):
        rows = base[t["task_id"]]; top = ranks[t["task_id"]]
        for g, row in zip(item["components"]["field"], rows, strict=True):
            row.update({"llm_rank1": 0.0, "llm_rank2": 0.0, "llm_rank3": 0.0, "llm_absent": 0.0})
            if g["value"] is None:
                continue
            if g["value"] in top:
                row["llm_rank%d" % (top.index(g["value"]) + 1)] = 1.0
            else:
                row["llm_absent"] = 1.0
        unknown = [i for i, g in enumerate(item["components"]["field"]) if g["value"] is None]
        w = torch.tensor([fit["weights"][n] for n in p31.FEATURES])
        prior[t["task_id"]] = {"field": p27.score(rows, p31.FEATURES, w, unknown[0] if unknown else None)}
    out = BANK / "prior_v33_field"; out.mkdir(exist_ok=True)
    save(out / "field_prior.json", prior)
    save(out / "manifest.json", {"bank": "heldout_v1/bank", "structural_prior_v27_sha256": json.loads((BANK / "prior_v27/manifest.json").read_text())["structural_prior_sha256"],
                                 "field_prior_sha256": sha(out / "field_prior.json"), "weights_from": str(FIELD33), "refit": False, "llm_ranking_answers": len(ranks), "llm_answers_dir": str(ANSWERS)})
    return out


def main():
    tasks = json.loads((BANK / "public/tasks.json").read_text())
    inventory = json.loads((BANK / "public/witnesses.json").read_text())
    print("v33 field ->", apply_field(tasks, inventory), flush=True)
    p32.DEST = PRIOR33
    out = p32.apply(BANK, HELD, BANK / "table_contexts.json", HELD / "schema/public/report_graphs.json", ANSWERS, BANK / "prior_v33_structural")
    print("v33 structural ->", out, flush=True)


if __name__ == "__main__":
    main()
