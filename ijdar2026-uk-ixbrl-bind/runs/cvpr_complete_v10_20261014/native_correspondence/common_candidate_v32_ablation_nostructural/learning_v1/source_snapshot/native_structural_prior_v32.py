"""v32: the v27 structural prior extended with the LLM's zero-shot period and dimension choices as features.

The deepseek-flash field-ranking pass (llm_baseline_v30 --mode field_rank) also recorded, for every
target, the index of the period and of the dimension tuple it would choose from the public candidate
lists. Those choices become one indicator feature per period group (llm_period_choice) and per
dimension group (llm_dims_choice); the v27 per-target scorers are refitted on the 2023 development
teachers with the extra feature (leave-one-company-out check). An `apply` entry point scores another
bank (the held-out companies) with the frozen weights. Nothing from reference or teacher files is
sent to the LLM.
"""

import json
from pathlib import Path

import numpy as np
import torch
import native_structural_prior_v27 as p27
from llm_baseline_v30 import candidate_lists
from native_dimension_network_v17 import DEST as NETWORK
from native_v30_bank import DEST as BANK
from native_v30_bank import PERCEPTION
from prepare import save, sha

DEST = BANK / "prior_v32"
RANKS = BANK / "llm_baseline_v30/field_rank/answers"
PERIOD_FEATURES = p27.PERIOD_FEATURES + ["llm_period_choice"]
DIM_FEATURES = p27.DIM_FEATURES + ["llm_dims_choice"]


def llm_choices(tasks, answers_dir):
    """Map the LLM's recorded indices back to semantic values (period dict / dimension list) per target."""
    chosen = {}
    for t in tasks:
        f = answers_dir / (t["task_id"] + ".json")
        if not f.exists():
            chosen[t["task_id"]] = (None, None); continue
        a = json.loads(f.read_text())
        _, _, periods, dims, _ = candidate_lists(t)
        p = d = None
        try:
            if a.get("period_index") is not None:
                p = {k: v for k, v in periods[int(a["period_index"])]["period"].items() if k != "complete"}
        except (IndexError, ValueError, TypeError):
            p = None
        try:
            if a.get("dimensions_index") is not None:
                d = sorted(dims[int(a["dimensions_index"])]["dimensions"], key=lambda x: x["axis"])
        except (IndexError, ValueError, TypeError):
            d = None
        chosen[t["task_id"]] = (p, d)
    return chosen


def build_features(tasks, records, inventory, pages, anchors, contexts, graphs, chosen):
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
    features = {}
    for task, record, item in zip(tasks, records, inventory, strict=True):
        words = pages[anchors[task["task_id"]]["page_hash"]]["words"]
        groups = item["components"]
        membership = {(r["axis"], r["member"]) for r in graphs[task["report"]]["axis_member_hints"]}
        periods = p27.period_features(task, groups["period"], [p27.meta(record["period"], g) for g in groups["period"]], words)
        dims = p27.dimension_features(task, [{"value": g["value"], "metadata": p27.meta(record["dimensions"], g)["metadata"]} for g in groups["dimensions"]],
                                      contexts[task["task_id"]], p27.member_labels(task), vectors, membership, p27.axis_labels(task))
        lp, ld = chosen.get(task["task_id"], (None, None))
        for g, row in zip(groups["period"], periods, strict=True):
            row["llm_period_choice"] = float(lp is not None and g["value"] == lp)
        for g, row in zip(groups["dimensions"], dims, strict=True):
            row["llm_dims_choice"] = float(ld is not None and g["value"] is not None and sorted(g["value"], key=lambda x: x["axis"]) == ld)
        features[task["task_id"]] = {"period": periods, "dimensions": dims}
    return features


def score_all(tasks, inventory, features, weights):
    prior = {}
    for t, item in zip(tasks, inventory, strict=True):
        prior[t["task_id"]] = {}
        for c, names in (("period", PERIOD_FEATURES), ("dimensions", DIM_FEATURES)):
            unknown = [i for i, g in enumerate(item["components"][c]) if g["value"] is None]
            w = torch.tensor([weights[c][n] for n in names])
            prior[t["task_id"]][c] = p27.score(features[t["task_id"]][c], names, w, unknown[0] if unknown else None)
    return prior


def load_bank(bank, perception, contexts_file, graphs_file):
    tasks = json.loads((bank / "public/tasks.json").read_text())
    records = json.loads((bank / "public/records.json").read_text())
    inventory = json.loads((bank / "public/witnesses.json").read_text())
    pages = json.loads((perception / "localization/public_pages.json").read_text())
    anchors = json.loads((perception / "localization/public_anchors.json").read_text())
    contexts = json.loads(contexts_file.read_text())
    graphs = json.loads(graphs_file.read_text())
    return tasks, records, inventory, pages, anchors, contexts, graphs


def main():
    if (DEST / "manifest.json").exists():
        raise RuntimeError("v32 structural prior already frozen")
    DEST.mkdir(parents=True, exist_ok=True)
    tasks, records, inventory, pages, anchors, contexts, graphs = load_bank(BANK, PERCEPTION, BANK / "prior_v27/table_contexts.json", NETWORK / "public/report_graphs.json")
    chosen = llm_choices(tasks, RANKS)
    print("LLM choices available:", sum(1 for v in chosen.values() if v[0] is not None), "period /", sum(1 for v in chosen.values() if v[1] is not None), "dims", flush=True)
    features = build_features(tasks, records, inventory, pages, anchors, contexts, graphs, chosen)
    teachers = json.loads((p27.TEACHERS / "training_only/past_binding_feedback.json").read_text())
    historical = [(t, item) for t, item in zip(tasks, inventory, strict=True) if t["source_year"] == 2023]
    companies = sorted({t["company"] for t, _ in historical})
    weights, report = {}, {}
    for channel, names, mode in (("period", PERIOD_FEATURES, "softmax"), ("dimensions", DIM_FEATURES, "binary")):
        def labels_for(t, groups):
            expected = (teachers[t["task_id"]].get("teacher_binding") or {}).get(channel)
            if expected is None:
                return [False] * len(groups)
            if channel == "dimensions":
                return [g["value"] is not None and sorted(g["value"], key=lambda d: d["axis"]) == sorted(expected, key=lambda d: d["axis"]) for g in groups]
            return [g["value"] == expected for g in groups]
        samples = {t["task_id"]: (features[t["task_id"]][channel], labels_for(t, item["components"][channel])) for t, item in historical}
        correct = counted = llm_only = 0
        for held in companies:
            train = [samples[t["task_id"]] for t, _ in historical if t["company"] != held]
            w, _ = p27.fit(train, names, mode=mode)
            for t, _ in historical:
                if t["company"] != held:
                    continue
                rows, label = samples[t["task_id"]]
                if not any(label):
                    continue
                counted += 1
                correct += bool(label[int(np.argmax(p27.score(rows, names, w)))])
                key = "llm_period_choice" if channel == "period" else "llm_dims_choice"
                llm_only += bool(label[int(np.argmax([r[key] for r in rows]))]) if any(r[key] for r in rows) else 0
        w, loss = p27.fit(list(samples.values()), names, mode=mode)
        weights[channel] = dict(zip(names, w.tolist()))
        report[channel] = {"leave_one_company_out_accuracy": correct / max(1, counted), "llm_choice_alone": llm_only / max(1, counted), "supervised_targets": counted, "final_loss": loss, "fit_mode": mode}
        print(channel, "LOCO %.3f (LLM choice alone %.3f) on %d" % (correct / max(1, counted), llm_only / max(1, counted), counted), flush=True)
    prior = score_all(tasks, inventory, features, weights)
    save(DEST / "structural_prior.json", prior)
    save(DEST / "structural_features.json", features)
    (DEST / "table_contexts.json").write_bytes((BANK / "prior_v27/table_contexts.json").read_bytes())
    save(DEST / "manifest.json", {"bank": "common_candidate_v30", "bank_witness_inventory_sha256": sha(BANK / "public/witnesses.json"), "structural_prior_sha256": sha(DEST / "structural_prior.json"),
                                  "fit": {c: {"weights": weights[c], **report[c]} for c in weights}, "period_features": PERIOD_FEATURES, "dimension_features": DIM_FEATURES,
                                  "llm": "deepseek-flash zero-shot period/dimension choices from the field_rank pass", "supervision": "2023 historical teachers only", "source_sha256": sha(Path(__file__))})
    print("v32 structural prior frozen", flush=True)


def apply(bank, perception, contexts_file, graphs_file, answers_dir, out):
    """Score another bank with the frozen v32 weights (no refit)."""
    fit = json.loads((DEST / "manifest.json").read_text())["fit"]
    tasks, records, inventory, pages, anchors, contexts, graphs = load_bank(bank, perception, contexts_file, graphs_file)
    chosen = llm_choices(tasks, answers_dir)
    features = build_features(tasks, records, inventory, pages, anchors, contexts, graphs, chosen)
    prior = score_all(tasks, inventory, features, {c: fit[c]["weights"] for c in fit})
    out.mkdir(parents=True, exist_ok=True)
    save(out / "structural_prior.json", prior); save(out / "structural_features.json", features)
    (out / "table_contexts.json").write_bytes(contexts_file.read_bytes())
    save(out / "manifest.json", {"bank": str(bank), "bank_witness_inventory_sha256": sha(bank / "public/witnesses.json"), "structural_prior_sha256": sha(out / "structural_prior.json"),
                                 "weights_from": str(DEST), "refit": False})
    return out


if __name__ == "__main__":
    main()
