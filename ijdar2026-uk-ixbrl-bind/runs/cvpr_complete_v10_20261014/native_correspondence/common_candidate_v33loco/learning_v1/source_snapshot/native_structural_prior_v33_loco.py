"""v33 priors fitted leave-one-company-out (LOCO) for the development evaluation.

The frozen v31/v32/v33 priors are fitted on the teachers of all fourteen development companies, so a company's
2024/25 targets are scored with weights that saw its own 2023 layout.  Here every target of company c is scored
with weights fitted on the teachers of the other thirteen companies only (the fold's weights are also used for
c's own 2023 teachers, so the composer's feedback cells carry no leakage either).  Features, fitting and scoring
code are the v31/v32 modules; only the weight assignment differs.  Inputs: the few-shot readings of v33.
Outputs: prior_v33loco_field/field_prior.json and prior_v33loco_structural/structural_prior.json in the v30 bank,
in the same formats as the frozen priors, plus manifests recording every fold's weights.
"""

import json
from pathlib import Path

import numpy as np
import torch
import native_structural_prior_v27 as p27
import native_structural_prior_v29 as p29
import native_structural_prior_v31 as p31
import native_structural_prior_v32 as p32
from native_dimension_network_v17 import DEST as NETWORK
from native_structural_prior_v30 import rebind
from native_structural_prior_v33 import RANKS
from native_v30_bank import DEST as BANK
from native_v30_bank import PERCEPTION
from prepare import save, sha

FIELD33L = BANK / "prior_v33loco_field"
PRIOR33L = BANK / "prior_v33loco_structural"


def field_prior(tasks, inventory, teachers):
    ranks = {}
    for f in RANKS.glob("*.json"):
        d = json.loads(f.read_text()); ranks[d["task_id"]] = d.get("field_top3") or []
    base = p31.build_v29_features(tasks, inventory)
    features = {}
    for t, item in zip(tasks, inventory, strict=True):
        rows = base[t["task_id"]]; top = ranks.get(t["task_id"], [])
        for g, row in zip(item["components"]["field"], rows, strict=True):
            row.update({"llm_rank1": 0.0, "llm_rank2": 0.0, "llm_rank3": 0.0, "llm_absent": 0.0})
            if g["value"] is None:
                continue
            if g["value"] in top:
                row["llm_rank%d" % (top.index(g["value"]) + 1)] = 1.0
            else:
                row["llm_absent"] = 1.0
        features[t["task_id"]] = rows
    historical = [(t, item) for t, item in zip(tasks, inventory, strict=True) if t["source_year"] == 2023]
    samples = {}
    for t, item in historical:
        expected = (teachers[t["task_id"]].get("teacher_binding") or {}).get("field")
        samples[t["task_id"]] = (features[t["task_id"]], [g["value"] == expected for g in item["components"]["field"]])
    companies = sorted({t["company"] for t in tasks})
    prior, folds, correct, counted = {}, {}, 0, 0
    for held in companies:
        train = [samples[t["task_id"]] for t, _ in historical if t["company"] != held]
        w, loss = p27.fit(train, p31.FEATURES, steps=400)
        folds[held] = {"weights": dict(zip(p31.FEATURES, w.tolist())), "final_loss": loss, "train_teachers": len(train)}
        for t, item in zip(tasks, inventory, strict=True):
            if t["company"] != held:
                continue
            unknown = [i for i, g in enumerate(item["components"]["field"]) if g["value"] is None]
            scores = p27.score(features[t["task_id"]], p31.FEATURES, w, unknown[0] if unknown else None)
            prior[t["task_id"]] = {"field": scores}
            if t["source_year"] == 2023 and any(samples[t["task_id"]][1]):
                counted += 1; correct += bool(samples[t["task_id"]][1][int(np.argmax(scores))])
    FIELD33L.mkdir(parents=True, exist_ok=True)
    save(FIELD33L / "field_prior.json", prior)
    save(FIELD33L / "manifest.json", {"bank": "common_candidate_v30", "field_prior_sha256": sha(FIELD33L / "field_prior.json"), "features": p31.FEATURES,
                                      "fit": {"leave_one_company_out": True, "folds": folds, "leave_one_company_out_accuracy": correct / max(1, counted), "supervised_targets": counted},
                                      "llm": "few-shot readings (llm_fewshot_rank_v1)", "llm_answers_dir": str(RANKS), "source_sha256": sha(Path(__file__))})
    print("v33 LOCO field prior: teacher LOCO accuracy %.3f on %d" % (correct / max(1, counted), counted), flush=True)


def structural_prior(teachers):
    tasks, records, inventory, pages, anchors, contexts, graphs = p32.load_bank(BANK, PERCEPTION, BANK / "prior_v27/table_contexts.json", NETWORK / "public/report_graphs.json")
    chosen = p32.llm_choices(tasks, RANKS)
    features = p32.build_features(tasks, records, inventory, pages, anchors, contexts, graphs, chosen)
    historical = [(t, item) for t, item in zip(tasks, inventory, strict=True) if t["source_year"] == 2023]
    companies = sorted({t["company"] for t in tasks})
    prior = {t["task_id"]: {} for t in tasks}
    folds, report = {c: {} for c in companies}, {}
    for channel, names, mode in (("period", p32.PERIOD_FEATURES, "softmax"), ("dimensions", p32.DIM_FEATURES, "binary")):
        def labels_for(t, groups):
            expected = (teachers[t["task_id"]].get("teacher_binding") or {}).get(channel)
            if expected is None:
                return [False] * len(groups)
            if channel == "dimensions":
                return [g["value"] is not None and sorted(g["value"], key=lambda d: d["axis"]) == sorted(expected, key=lambda d: d["axis"]) for g in groups]
            return [g["value"] == expected for g in groups]
        samples = {t["task_id"]: (features[t["task_id"]][channel], labels_for(t, item["components"][channel])) for t, item in historical}
        correct = counted = 0
        for held in companies:
            train = [samples[t["task_id"]] for t, _ in historical if t["company"] != held]
            w, loss = p27.fit(train, names, mode=mode)
            folds[held][channel] = {"weights": dict(zip(names, w.tolist())), "final_loss": loss, "train_teachers": len(train)}
            for t, item in zip(tasks, inventory, strict=True):
                if t["company"] != held:
                    continue
                unknown = [i for i, g in enumerate(item["components"][channel]) if g["value"] is None]
                scores = p27.score(features[t["task_id"]][channel], names, w, unknown[0] if unknown else None)
                prior[t["task_id"]][channel] = scores
                if t["source_year"] == 2023 and any(samples[t["task_id"]][1]):
                    counted += 1; correct += bool(samples[t["task_id"]][1][int(np.argmax(scores))])
        report[channel] = {"leave_one_company_out_accuracy": correct / max(1, counted), "supervised_targets": counted, "fit_mode": mode}
        print(channel, "v33 LOCO structural prior: teacher LOCO accuracy %.3f on %d" % (correct / max(1, counted), counted), flush=True)
    PRIOR33L.mkdir(parents=True, exist_ok=True)
    save(PRIOR33L / "structural_prior.json", prior)
    save(PRIOR33L / "structural_features.json", features)
    (PRIOR33L / "table_contexts.json").write_bytes((BANK / "prior_v27/table_contexts.json").read_bytes())
    save(PRIOR33L / "manifest.json", {"bank": "common_candidate_v30", "bank_witness_inventory_sha256": sha(BANK / "public/witnesses.json"), "structural_prior_sha256": sha(PRIOR33L / "structural_prior.json"),
                                      "fit": {"leave_one_company_out": True, "folds": folds, **report}, "period_features": p32.PERIOD_FEATURES, "dimension_features": p32.DIM_FEATURES,
                                      "llm": "few-shot readings (llm_fewshot_rank_v1)", "llm_answers_dir": str(RANKS), "source_sha256": sha(Path(__file__))})


def main():
    for folder in (FIELD33L, PRIOR33L):
        if (folder / "manifest.json").exists():
            raise RuntimeError("already frozen: " + str(folder))
    if len(list(RANKS.glob("*.json"))) != 840:
        raise RuntimeError("few-shot rank answers incomplete")
    rebind()
    tasks = json.loads((BANK / "public/tasks.json").read_text())
    inventory = json.loads((BANK / "public/witnesses.json").read_text())
    teachers = json.loads((p29.TEACHERS / "training_only/past_binding_feedback.json").read_text())
    field_prior(tasks, inventory, teachers)
    structural_prior(teachers)
    print("v33 LOCO priors frozen", flush=True)


if __name__ == "__main__":
    main()
