"""Held-out companies, step F: apply the development-fitted priors (v27 structural, v28 pair, v31 field).

No refitting: the weights frozen on the 14 development companies' 2023 teachers are applied to
the held-out features. Outputs mirror the development prior folders under heldout_v1/bank/prior_v2{7,8}
and prior_v31, so the held-out loader can add them exactly as native_v27/v28/v29 data do.
"""

import json
import os
import shutil
from pathlib import Path

import numpy as np
import torch
import native_structural_prior_v27 as p27
import native_structural_prior_v28 as p28
import native_structural_prior_v29 as p29
import native_structural_prior_v31 as p31
from native_v30_bank import DEST as DEV_BANK
from prepare import save, sha

HELD = Path(os.environ.get("HELDOUT_ROOT", str(Path(__file__).resolve().parents[1] / "runs/cvpr_complete_v10_20261014/native_correspondence/heldout_v1")))
BANK = HELD / "bank"
DEV27, DEV28, DEV31 = DEV_BANK / "prior_v27", DEV_BANK / "prior_v28", DEV_BANK / "prior_v31"


def apply_v27(tasks, records, inventory, pages, anchors, contexts, graphs):
    fit = json.loads((DEV27 / "manifest.json").read_text())["fit"]
    strings = set()
    for task, item in zip(tasks, inventory, strict=True):
        labels, axes = p27.member_labels(task), p27.axis_labels(task)
        for g in item["components"]["dimensions"]:
            if g["value"]:
                strings.update(p27.member_names(labels, g["value"])); strings.update(p27.axis_names(axes, g["value"]))
        ctx = contexts[task["task_id"]]
        strings.update(c for c in ctx["column"] if c); strings.update(b for b in ctx["blocks"] if b)
        if ctx["row"]:
            strings.add(ctx["row"])
    strings.discard("")
    vectors = p27.encode_texts(sorted(strings))
    prior, features = {}, {}
    for task, record, item in zip(tasks, records, inventory, strict=True):
        words = pages[anchors[task["task_id"]]["page_hash"]]["words"]
        groups = item["components"]
        membership = {(r["axis"], r["member"]) for r in graphs[task["report"]]["axis_member_hints"]}
        feats = {"period": p27.period_features(task, groups["period"], [p27.meta(record["period"], g) for g in groups["period"]], words),
                 "dimensions": p27.dimension_features(task, [{"value": g["value"], "metadata": p27.meta(record["dimensions"], g)["metadata"]} for g in groups["dimensions"]],
                                                      contexts[task["task_id"]], p27.member_labels(task), vectors, membership, p27.axis_labels(task))}
        features[task["task_id"]] = feats
        prior[task["task_id"]] = {}
        for c, names in (("period", p27.PERIOD_FEATURES), ("dimensions", p27.DIM_FEATURES)):
            unknown = [i for i, g in enumerate(groups[c]) if g["value"] is None]
            w = torch.tensor([fit[c]["weights"][n] for n in names])
            prior[task["task_id"]][c] = p27.score(feats[c], names, w, unknown[0] if unknown else None)
    out = BANK / "prior_v27"; out.mkdir(exist_ok=True)
    save(out / "structural_prior.json", prior); save(out / "structural_features.json", features); save(out / "table_contexts.json", contexts)
    save(out / "manifest.json", {"bank": "heldout_v1/bank", "bank_witness_inventory_sha256": sha(BANK / "public/witnesses.json"), "structural_prior_sha256": sha(out / "structural_prior.json"),
                                 "weights_from": str(DEV27), "refit": False})
    return out


def apply_v28(tasks, records, inventory, pages, anchors):
    fit = json.loads((DEV28 / "manifest.json").read_text())["fit"]
    prior = {}
    for task, record, item in zip(tasks, records, inventory, strict=True):
        words = pages[anchors[task["task_id"]]["page_hash"]]["words"]
        groups = item["components"]["unit_quantity"]
        rec = [record["unit_quantity"][g["source_record_indices"][0]] if g["value"] is not None and g["source_record_indices"] and g["source_record_indices"][0] < len(record["unit_quantity"]) else {"metadata": {}} for g in groups]
        q = p28.quantity_features(task, groups, rec, words)
        codes = [list(p28.field_codes(record["field"][g["source_record_indices"][0]]["metadata"])) if g["value"] is not None else [2, 3] for g in item["components"]["field"]]
        prior[task["task_id"]] = {"pair_logits": p28.pair_logits(q, fit["weights"], fit["intercept"]).tolist(), "field_codes": codes}
    out = BANK / "prior_v28"; out.mkdir(exist_ok=True)
    save(out / "unit_quantity_pair_prior.json", prior)
    save(out / "manifest.json", {"bank": "heldout_v1/bank", "structural_prior_v27_sha256": json.loads((BANK / "prior_v27/manifest.json").read_text())["structural_prior_sha256"],
                                 "unit_quantity_pair_prior_sha256": sha(out / "unit_quantity_pair_prior.json"), "weights_from": str(DEV28), "refit": False})
    return out


def apply_v31(tasks, inventory):
    fit = json.loads((DEV31 / "manifest.json").read_text())["fit"]
    p29.BANK, p29.ALIGN, p29.V27, p29.TEACHERS = BANK, HELD, BANK / "prior_v27", HELD
    ranks = {}
    for f in (HELD / "llm/field_rank/answers").glob("*.json"):
        d = json.loads(f.read_text()); ranks[d["task_id"]] = d.get("field_top3") or []
    if len(ranks) != len(tasks):
        raise ValueError("LLM field ranking incomplete for the held-out targets: %d / %d" % (len(ranks), len(tasks)))
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
    out = BANK / "prior_v31"; out.mkdir(exist_ok=True)
    save(out / "field_prior.json", prior)
    save(out / "manifest.json", {"bank": "heldout_v1/bank", "structural_prior_v27_sha256": json.loads((BANK / "prior_v27/manifest.json").read_text())["structural_prior_sha256"],
                                 "field_prior_sha256": sha(out / "field_prior.json"), "weights_from": str(DEV31), "refit": False, "llm_ranking_answers": len(ranks)})
    return out


def main():
    tasks = json.loads((BANK / "public/tasks.json").read_text())
    records = json.loads((BANK / "public/records.json").read_text())
    inventory = json.loads((BANK / "public/witnesses.json").read_text())
    pages = json.loads((HELD / "localization/public_pages.json").read_text())
    anchors = json.loads((HELD / "localization/public_anchors.json").read_text())
    contexts = json.loads((BANK / "table_contexts.json").read_text())
    graphs = json.loads((HELD / "schema/public/report_graphs.json").read_text())
    print("v27 ->", apply_v27(tasks, records, inventory, pages, anchors, contexts, graphs), flush=True)
    print("v28 ->", apply_v28(tasks, records, inventory, pages, anchors), flush=True)
    print("v31 ->", apply_v31(tasks, inventory), flush=True)


if __name__ == "__main__":
    main()
