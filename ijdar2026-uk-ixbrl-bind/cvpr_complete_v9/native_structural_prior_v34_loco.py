"""v34 field prior fitted leave-one-company-out (LOCO) for the development evaluation.

The frozen v34 field prior (native_structural_prior_v34) is fitted on the teachers of all fourteen development
companies, so a company's 2024/25 targets are scored with weights that saw its own 2023 layout.  Here every task of
company c (its 2023 teachers and its 2024/25 targets) is scored with weights fitted on the teachers of the other thirteen
companies only.  Features, fitting (native_structural_prior_v27.fit, 400 steps) and scoring are those of v34; only the
weight assignment differs.  The structural prior (period/dimensions) is the v33 LOCO prior
(native_structural_prior_v33_loco), so no prior of v34loco has seen the evaluated company.
Output: prior_v34loco_field/field_prior.json in the v30 bank, same format as the frozen prior, plus a manifest with
every fold's weights.  2024/25 references are never read.
"""

import json
from pathlib import Path

import numpy as np
import native_structural_prior_v29 as p29
import native_structural_prior_v31 as p31
import native_presentation_v34 as lb
from native_structural_prior_v27 import fit, score
from native_structural_prior_v30 import rebind
from native_structural_prior_v33 import RANKS
from native_structural_prior_v34 import FEATURES
from native_v30_bank import DEST as BANK
from prepare import save, sha

DEST = BANK / "prior_v34loco_field"


def main():
    if (DEST / "manifest.json").exists():
        raise RuntimeError("v34 LOCO field prior already frozen")
    rebind()
    lbm = json.loads((lb.DEST / "manifest.json").read_text())
    if sha(lb.DEST / "linkbase_features.json") != lbm["linkbase_features_sha256"]:
        raise ValueError("Frozen v34 linkbase features changed")
    linkbase = json.loads((lb.DEST / "linkbase_features.json").read_text())
    p31.RANKS = RANKS
    ranks = p31.llm_ranks()
    tasks = json.loads((BANK / "public/tasks.json").read_text()); inventory = json.loads((BANK / "public/witnesses.json").read_text())
    if len(ranks) != 840:
        raise ValueError("few-shot field ranking incomplete: %d / 840" % len(ranks))
    base = p31.build_v29_features(tasks, inventory)
    features = {}
    for t, item in zip(tasks, inventory, strict=True):
        rows = base[t["task_id"]]; top = ranks.get(t["task_id"], []); extra = linkbase[t["task_id"]]
        for g, row, add in zip(item["components"]["field"], rows, extra, strict=True):
            row.update({"llm_rank1": 0.0, "llm_rank2": 0.0, "llm_rank3": 0.0, "llm_absent": 0.0}); row.update(add)
            if g["value"] is None:
                continue
            if g["value"] in top:
                row["llm_rank%d" % (top.index(g["value"]) + 1)] = 1.0
            else:
                row["llm_absent"] = 1.0
        features[t["task_id"]] = rows
    teachers = json.loads((p29.TEACHERS / "training_only/past_binding_feedback.json").read_text())
    historical = [(t, item) for t, item in zip(tasks, inventory, strict=True) if t["source_year"] == 2023]
    samples = {t["task_id"]: (features[t["task_id"]], [g["value"] == (teachers[t["task_id"]].get("teacher_binding") or {}).get("field") for g in item["components"]["field"]]) for t, item in historical}
    companies = sorted({t["company"] for t in tasks})
    prior, folds, correct, counted = {}, {}, 0, 0
    for held in companies:
        train = [samples[t["task_id"]] for t, _ in historical if t["company"] != held]
        w, loss = fit(train, FEATURES, steps=400)
        folds[held] = {"weights": dict(zip(FEATURES, w.tolist())), "final_loss": loss, "train_teachers": len(train)}
        for t, item in zip(tasks, inventory, strict=True):
            if t["company"] != held:
                continue
            unknown = [i for i, g in enumerate(item["components"]["field"]) if g["value"] is None]
            prior[t["task_id"]] = {"field": score(features[t["task_id"]], FEATURES, w, unknown[0] if unknown else None)}
            if t["source_year"] == 2023 and any(samples[t["task_id"]][1]):
                counted += 1; correct += bool(samples[t["task_id"]][1][int(np.argmax(prior[t["task_id"]]["field"]))])
    DEST.mkdir(parents=True, exist_ok=True)
    save(DEST / "field_prior.json", prior)
    save(DEST / "manifest.json", {"bank": "common_candidate_v30", "field_prior_sha256": sha(DEST / "field_prior.json"), "features": FEATURES,
                                  "linkbase_features_sha256": lbm["linkbase_features_sha256"], "llm_answers_dir": str(RANKS),
                                  "fit": {"leave_one_company_out": True, "folds": folds, "leave_one_company_out_accuracy": correct / max(1, counted), "supervised_targets": counted},
                                  "supervision": "2023 historical teachers of the other thirteen companies; 2024/25 references never read",
                                  "source_sha256": sha(Path(__file__))})
    print("v34 LOCO field prior: teacher LOCO accuracy %.3f on %d" % (correct / max(1, counted), counted), flush=True)


if __name__ == "__main__":
    main()
