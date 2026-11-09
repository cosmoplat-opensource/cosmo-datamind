"""v34 field prior WITHOUT any language-model feature: the v29 feature set plus the presentation/calculation-
linkbase features of native_presentation_v34 (the NO_LLM set whose leave-one-company-out accuracy is reported in
the v34 manifest).  Fitted exactly as v29/v31/v34: a per-target softmax scorer on the 2023 teachers, LOCO check,
centred logits saved as the field prior.  No LLM answer file is opened; 2024/25 references are never read.
The matching system (native_v34_nollm_data) pairs it with the v27 structural prior and the v28 pair prior, i.e.
the v30 system plus the linkbases: "can the taxonomy's own presentation structure replace the reader?"
"""

import json
from pathlib import Path

import native_structural_prior_v29 as p29
import native_structural_prior_v31 as p31
import native_presentation_v34 as lb
from native_structural_prior_v27 import fit, score
from native_structural_prior_v30 import rebind
from native_structural_prior_v34 import NO_LLM, loco
from native_v30_bank import DEST as BANK
from prepare import save, sha

DEST = BANK / "prior_v34_nollm_field"
FEATURES = list(NO_LLM)


def main():
    if (DEST / "manifest.json").exists():
        raise RuntimeError("v34 no-LLM field prior already frozen")
    rebind()
    lbm = json.loads((lb.DEST / "manifest.json").read_text())
    if sha(lb.DEST / "linkbase_features.json") != lbm["linkbase_features_sha256"]:
        raise ValueError("Frozen v34 linkbase features changed")
    linkbase = json.loads((lb.DEST / "linkbase_features.json").read_text())
    tasks = json.loads((BANK / "public/tasks.json").read_text()); inventory = json.loads((BANK / "public/witnesses.json").read_text())
    base = p31.build_v29_features(tasks, inventory)
    features = {}
    for t, item in zip(tasks, inventory, strict=True):
        rows = base[t["task_id"]]; extra = linkbase[t["task_id"]]
        for row, add in zip(rows, extra, strict=True):
            row.update(add)
        features[t["task_id"]] = rows
    teachers = json.loads((p29.TEACHERS / "training_only/past_binding_feedback.json").read_text())
    historical = [(t, item) for t, item in zip(tasks, inventory, strict=True) if t["source_year"] == 2023]
    samples = {t["task_id"]: (features[t["task_id"]], [g["value"] == (teachers[t["task_id"]].get("teacher_binding") or {}).get("field") for g in item["components"]["field"]]) for t, item in historical}
    acc, counted = loco(samples, historical, FEATURES)
    acc29, _ = loco(samples, historical, list(p29.FEATURES))
    print("v34 no-LLM field LOCO accuracy %.3f (v29 features %.3f) on %d historical targets" % (acc, acc29, counted), flush=True)
    w, loss = fit(list(samples.values()), FEATURES, steps=400)
    DEST.mkdir(parents=True, exist_ok=True)
    prior = {}
    for t, item in zip(tasks, inventory, strict=True):
        unknown = [i for i, g in enumerate(item["components"]["field"]) if g["value"] is None]
        prior[t["task_id"]] = {"field": score(features[t["task_id"]], FEATURES, w, unknown[0] if unknown else None)}
    save(DEST / "field_prior.json", prior)
    save(DEST / "manifest.json", {"bank": "common_candidate_v30", "field_prior_sha256": sha(DEST / "field_prior.json"), "linkbase_features_sha256": lbm["linkbase_features_sha256"],
                                  "llm": "none (no language-model feature; no LLM answer file opened)",
                                  "fit": {"weights": dict(zip(FEATURES, w.tolist())), "final_loss": loss, "leave_one_company_out_accuracy": acc, "v29_features_loco_accuracy": acc29, "supervised_targets": counted},
                                  "features": FEATURES, "supervision": "2023 historical teachers only; 2024/25 references never read", "source_sha256": sha(Path(__file__))})
    print("v34 no-LLM field prior frozen", flush=True)


if __name__ == "__main__":
    main()
