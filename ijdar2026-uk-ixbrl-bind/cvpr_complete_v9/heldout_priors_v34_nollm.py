"""Held-out companies: apply the development-fitted v34 NO-LLM field prior (v29 features + presentation/calculation-
linkbase features, no language-model feature) without refitting.  The structural prior for the matching system is the
development-fitted v27 structural prior (heldout_v1/bank/prior_v27).  No LLM answer file is opened.
Run the predictor afterwards with
  HELDOUT_DEV_LOADER=v34_nollm HELDOUT_STRUCTURAL=prior_v27 HELDOUT_FIELD=prior_v34_nollm_field HELDOUT_EVAL_FOLDER=evaluation_v5
"""

import json

import torch
import native_structural_prior_v27 as p27
import native_structural_prior_v29 as p29
import native_structural_prior_v31 as p31
import native_presentation_v34 as lb
from heldout_priors_v1 import BANK, HELD
from heldout_priors_v34 import heldout_links
from native_structural_prior_v34_nollm import DEST as FIELD34N, FEATURES
from prepare import save, sha


def main():
    fit = json.loads((FIELD34N / "manifest.json").read_text())["fit"]
    tasks = json.loads((BANK / "public/tasks.json").read_text()); inventory = json.loads((BANK / "public/witnesses.json").read_text())
    records = json.loads((BANK / "public/records.json").read_text())
    pages = json.loads((HELD / "localization/public_pages.json").read_text()); anchors = json.loads((HELD / "localization/public_anchors.json").read_text())
    p29.BANK, p29.ALIGN, p29.V27, p29.TEACHERS = BANK, HELD, BANK / "prior_v27", HELD
    links, pkg_manifest = heldout_links({t["report"] for t in tasks})
    base = p31.build_v29_features(tasks, inventory)
    w = torch.tensor([fit["weights"][n] for n in FEATURES])
    prior = {}; presented = 0
    for t, item, rec in zip(tasks, inventory, records, strict=True):
        rows = base[t["task_id"]]
        extra = lb.candidate_features(t, item, rec, pages[anchors[t["task_id"]]["page_hash"]]["words"], links[t["report"]])
        for row, add in zip(rows, extra, strict=True):
            row.update(add); presented += add["pre_statement"]
        unknown = [i for i, g in enumerate(item["components"]["field"]) if g["value"] is None]
        prior[t["task_id"]] = {"field": p27.score(rows, FEATURES, w, unknown[0] if unknown else None)}
    out = BANK / "prior_v34_nollm_field"; out.mkdir(exist_ok=True)
    save(out / "field_prior.json", prior)
    save(out / "manifest.json", {"bank": "heldout_v1/bank", "field_prior_sha256": sha(out / "field_prior.json"), "weights_from": str(FIELD34N), "refit": False,
                                 "llm": "none", "packages": pkg_manifest, "members_read": "_pre.xml and _cal.xml only",
                                 "mean_candidates_in_statement_networks": presented / len(tasks), "features": FEATURES})
    print("v34 no-LLM field ->", out, "mean presented candidates %.1f" % (presented / len(tasks)), flush=True)


if __name__ == "__main__":
    main()
