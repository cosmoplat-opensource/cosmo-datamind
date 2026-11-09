"""v34 field prior: the v33 field prior (v29 features + few-shot LLM rank indicators) extended with the
presentation/calculation-linkbase features of native_presentation_v34.  Fitted exactly as v31/v33: a
per-target softmax scorer on the 2023 teachers, leave-one-company-out check, centred logits saved as the
field prior.  The structural prior (period/dimensions) stays v33.  2024/25 references are never read.
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
from native_v30_bank import DEST as BANK
from prepare import save, sha

DEST = BANK / "prior_v34_field"
FEATURES = p31.FEATURES + lb.FEATURES
NO_LLM = p29.FEATURES + lb.FEATURES


def loco(samples, historical, features):
    companies = sorted({t["company"] for t, _ in historical}); correct = counted = 0
    for held in companies:
        w, _ = fit([samples[t["task_id"]] for t, _ in historical if t["company"] != held], features, steps=400)
        for t, _ in historical:
            if t["company"] != held:
                continue
            rows, label = samples[t["task_id"]]
            if not any(label):
                continue
            counted += 1; correct += bool(label[int(np.argmax(score(rows, features, w)))])
    return correct / max(1, counted), counted


def main():
    if (DEST / "manifest.json").exists():
        raise RuntimeError("v34 field prior already frozen")
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
    acc, counted = loco(samples, historical, FEATURES)
    acc33, _ = loco(samples, historical, p31.FEATURES)
    acc_nollm, _ = loco(samples, historical, NO_LLM)
    print("v34 field LOCO accuracy %.3f (v33 features %.3f, no-LLM + linkbase %.3f) on %d historical targets" % (acc, acc33, acc_nollm, counted), flush=True)
    w, loss = fit(list(samples.values()), FEATURES, steps=400)
    DEST.mkdir(parents=True, exist_ok=True)
    prior = {}
    for t, item in zip(tasks, inventory, strict=True):
        unknown = [i for i, g in enumerate(item["components"]["field"]) if g["value"] is None]
        prior[t["task_id"]] = {"field": score(features[t["task_id"]], FEATURES, w, unknown[0] if unknown else None)}
    save(DEST / "field_prior.json", prior)
    save(DEST / "manifest.json", {"bank": "common_candidate_v30", "field_prior_sha256": sha(DEST / "field_prior.json"), "linkbase_features_sha256": lbm["linkbase_features_sha256"],
                                  "llm_answers_dir": str(RANKS), "llm": "deepseek-flash few-shot field ranking (llm_fewshot_rank_v1), vision input, same public lists",
                                  "fit": {"weights": dict(zip(FEATURES, w.tolist())), "final_loss": loss, "leave_one_company_out_accuracy": acc, "v33_features_loco_accuracy": acc33,
                                          "no_llm_plus_linkbase_loco_accuracy": acc_nollm, "supervised_targets": counted},
                                  "features": FEATURES, "supervision": "2023 historical teachers only; 2024/25 references never read", "source_sha256": sha(Path(__file__))})
    print("v34 field prior frozen", flush=True)


if __name__ == "__main__":
    main()
