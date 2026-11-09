"""Held-out companies: apply the development-fitted v34 field prior (few-shot LLM readings + presentation/
calculation-linkbase features) without refitting.  The structural prior stays v33 (prior_v33_structural).

Linkbase features need the held-out report packages' _pre.xml/_cal.xml.  They are looked up as
  $HELDOUT_PACKAGES/<report>/package.zip   (default: heldout_v1/packages)
and each package is checksum-recorded in the output manifest.  Only the two declaration linkbases are read.
Run the predictor afterwards with
  HELDOUT_DEV_LOADER=v34 HELDOUT_STRUCTURAL=prior_v33_structural HELDOUT_FIELD=prior_v34_field HELDOUT_EVAL_FOLDER=evaluation_v4
"""

import json
import os
from pathlib import Path

import torch
import native_structural_prior_v27 as p27
import native_structural_prior_v29 as p29
import native_structural_prior_v31 as p31
import native_presentation_v34 as lb
from heldout_priors_v1 import BANK, HELD
from native_structural_prior_v34 import DEST as FIELD34, FEATURES
from prepare import save, sha

ANSWERS = HELD / "llm/fewshot_rank_v1/answers"
PACKAGES = Path(os.environ.get("HELDOUT_PACKAGES", str(HELD / "packages")))


def heldout_links(reports):
    links, manifest = {}, {}
    for r in sorted(reports):
        z = PACKAGES / r / "package.zip"
        if not z.exists():
            raise FileNotFoundError("held-out package missing: " + str(z))
        links[r] = lb.parse_linkbases(z); manifest[r] = {"package": str(z), "sha256": sha(z), "statement_concepts": sum(e["pre_statement"] for e in links[r].values())}
    return links, manifest


def main():
    fit = json.loads((FIELD34 / "manifest.json").read_text())["fit"]
    tasks = json.loads((BANK / "public/tasks.json").read_text()); inventory = json.loads((BANK / "public/witnesses.json").read_text())
    records = json.loads((BANK / "public/records.json").read_text())
    pages = json.loads((HELD / "localization/public_pages.json").read_text()); anchors = json.loads((HELD / "localization/public_anchors.json").read_text())
    p29.BANK, p29.ALIGN, p29.V27, p29.TEACHERS = BANK, HELD, BANK / "prior_v27", HELD
    ranks = {}
    for f in ANSWERS.glob("*.json"):
        d = json.loads(f.read_text()); ranks[d["task_id"]] = d.get("field_top3") or []
    if len(ranks) != len(tasks):
        raise ValueError("few-shot ranking incomplete for the held-out targets: %d / %d" % (len(ranks), len(tasks)))
    links, pkg_manifest = heldout_links({t["report"] for t in tasks})
    base = p31.build_v29_features(tasks, inventory)
    w = torch.tensor([fit["weights"][n] for n in FEATURES])
    prior = {}; presented = 0
    for t, item, rec in zip(tasks, inventory, records, strict=True):
        rows = base[t["task_id"]]; top = ranks[t["task_id"]]
        extra = lb.candidate_features(t, item, rec, pages[anchors[t["task_id"]]["page_hash"]]["words"], links[t["report"]])
        for g, row, add in zip(item["components"]["field"], rows, extra, strict=True):
            row.update({"llm_rank1": 0.0, "llm_rank2": 0.0, "llm_rank3": 0.0, "llm_absent": 0.0}); row.update(add)
            presented += add["pre_statement"]
            if g["value"] is None:
                continue
            if g["value"] in top:
                row["llm_rank%d" % (top.index(g["value"]) + 1)] = 1.0
            else:
                row["llm_absent"] = 1.0
        unknown = [i for i, g in enumerate(item["components"]["field"]) if g["value"] is None]
        prior[t["task_id"]] = {"field": p27.score(rows, FEATURES, w, unknown[0] if unknown else None)}
    out = BANK / "prior_v34_field"; out.mkdir(exist_ok=True)
    save(out / "field_prior.json", prior)
    save(out / "manifest.json", {"bank": "heldout_v1/bank", "field_prior_sha256": sha(out / "field_prior.json"), "weights_from": str(FIELD34), "refit": False,
                                 "llm_ranking_answers": len(ranks), "llm_answers_dir": str(ANSWERS), "packages": pkg_manifest, "members_read": "_pre.xml and _cal.xml only",
                                 "mean_candidates_in_statement_networks": presented / len(tasks), "features": FEATURES})
    print("v34 field ->", out, "mean presented candidates %.1f" % (presented / len(tasks)), flush=True)


if __name__ == "__main__":
    main()
