"""v31: the v29 field prior extended with the zero-shot LLM field ranking as features.

For every target the deepseek-flash field-ranking pass (llm_baseline_v30.py --mode
field_rank, vision input, same public candidate list) supplies up to three ranked
concepts. Each field candidate receives indicator features llm_rank1/2/3 and
llm_absent; the per-target softmax scorer of v29 is refitted on the 2023 teachers
with these features added (leave-one-company-out check), and the centred logits
replace the v29 field prior. The LLM never sees reference or teacher files.
"""

import json
from pathlib import Path

import numpy as np
import torch
import native_structural_prior_v29 as p29
from native_structural_prior_v27 import fit, score
from native_structural_prior_v30 import PRIOR27, rebind
from native_v30_bank import DEST as BANK
from prepare import save, sha

DEST = BANK / "prior_v31"
RANKS = BANK / "llm_baseline_v30/field_rank/answers"
FEATURES = p29.FEATURES + ["llm_rank1", "llm_rank2", "llm_rank3", "llm_absent"]


def llm_ranks():
    out = {}
    for f in RANKS.glob("*.json"):
        d = json.loads(f.read_text())
        out[d["task_id"]] = d.get("field_top3") or []
    return out


def build_v29_features(tasks, inventory):
    """Recompute the v29 field features on the v30 bank (same code path as native_structural_prior_v29.main)."""
    from collections import defaultdict
    records = json.loads((p29.BANK / "public/records.json").read_text())
    pages = json.loads((p29.ALIGN / "localization/public_pages.json").read_text())
    anchors = json.loads((p29.ALIGN / "localization/public_anchors.json").read_text())
    teachers = json.loads((p29.TEACHERS / "training_only/past_binding_feedback.json").read_text())
    contexts = json.loads((p29.V27 / "table_contexts.json").read_text())
    structural = json.loads((p29.V27 / "structural_prior.json").read_text())
    rows_text, strings = {}, set()
    for task in tasks:
        words = pages[anchors[task["task_id"]]["page_hash"]]["words"]
        rows_text[task["task_id"]] = p29.row_label(words, task["target_box"])
        strings.add(rows_text[task["task_id"]]); strings.add(p29.norm(rows_text[task["task_id"]]))
        strings.update(c for c in contexts[task["task_id"]]["column"] if c); strings.update(b for b in contexts[task["task_id"]]["blocks"] if b)
    for record in records:
        for r in record["field"]:
            m = r["metadata"]
            strings.update(l for l in (m.get("labels") or []) if l)
            if not m.get("labels"):
                strings.add(p29.camel(m.get("name", "")))
    strings.discard("")
    vectors = p29.encode_texts(sorted(strings))
    entries = defaultdict(list)
    for task in tasks:
        if task["source_year"] == 2023 and (teachers[task["task_id"]].get("teacher_binding") or {}).get("field"):
            entries[task["company"]].append((task["task_id"], p29.norm(rows_text[task["task_id"]]), teachers[task["task_id"]]["teacher_binding"]["field"]))

    def negative_glyph(task):
        glyphs = [(q.get("source_glyph_hypotheses") or [""])[0].strip() for q in task["quantity_candidates"]]
        return any(g.startswith("(") or g.startswith("-") or g.startswith("–") for g in glyphs)

    features = {}
    for task, record, item in zip(tasks, records, inventory, strict=True):
        groups = item["components"]["field"]
        recs = [record["field"][g["source_record_indices"][0]]["metadata"] if g["value"] is not None and g["source_record_indices"] and g["source_record_indices"][0] < len(record["field"]) else {} for g in groups]
        pv = [g["value"] for g in item["components"]["period"]]
        sp = structural[task["task_id"]]["period"]
        kind = (pv[int(np.argmax(sp))] or {}).get("kind") if pv else None
        memory = p29.company_memory(entries[task["company"]], rows_text[task["task_id"]], vectors, exclude=task["task_id"] if task["source_year"] == 2023 else None)
        features[task["task_id"]] = p29.field_features(task, groups, recs, contexts[task["task_id"]], rows_text[task["task_id"]], vectors, kind, negative_glyph(task), memory)
    return features


def main():
    if (DEST / "manifest.json").exists():
        raise RuntimeError("v31 field prior already frozen")
    rebind()
    DEST.mkdir(parents=True, exist_ok=True)
    ranks = llm_ranks()
    tasks = json.loads((BANK / "public/tasks.json").read_text())
    inventory = json.loads((BANK / "public/witnesses.json").read_text())
    if len(ranks) != 840:
        raise ValueError("LLM field ranking incomplete: %d / 840" % len(ranks))
    base_features = build_v29_features(tasks, inventory)
    features = {}
    for t, item in zip(tasks, inventory, strict=True):
        rows = base_features[t["task_id"]]
        top = ranks.get(t["task_id"], [])
        for g, row in zip(item["components"]["field"], rows, strict=True):
            row.update({"llm_rank1": 0.0, "llm_rank2": 0.0, "llm_rank3": 0.0, "llm_absent": 0.0})
            if g["value"] is None:
                continue
            if g["value"] in top:
                row["llm_rank%d" % (top.index(g["value"]) + 1)] = 1.0
            else:
                row["llm_absent"] = 1.0
        features[t["task_id"]] = rows
    teachers = json.loads((p29.TEACHERS / "training_only/past_binding_feedback.json").read_text())
    historical = [(t, item) for t, item in zip(tasks, inventory, strict=True) if t["source_year"] == 2023]
    samples = {}
    for t, item in historical:
        expected = (teachers[t["task_id"]].get("teacher_binding") or {}).get("field")
        samples[t["task_id"]] = (features[t["task_id"]], [g["value"] == expected for g in item["components"]["field"]])
    companies = sorted({t["company"] for t, _ in historical})
    correct = counted = llm_only = 0
    for held in companies:
        train = [samples[t["task_id"]] for t, _ in historical if t["company"] != held]
        w, _ = fit(train, FEATURES, steps=400)
        for t, _ in historical:
            if t["company"] != held:
                continue
            rows, label = samples[t["task_id"]]
            if not any(label):
                continue
            counted += 1
            correct += bool(label[int(np.argmax(score(rows, FEATURES, w)))])
            llm_only += bool(label[int(np.argmax([r["llm_rank1"] for r in rows]))]) if any(r["llm_rank1"] for r in rows) else 0
    w, loss = fit(list(samples.values()), FEATURES, steps=400)
    print("v31 field LOCO accuracy %.3f (LLM rank-1 alone %.3f) on %d historical targets" % (correct / max(1, counted), llm_only / max(1, counted), counted), flush=True)
    prior = {}
    for t, item in zip(tasks, inventory, strict=True):
        unknown = [i for i, g in enumerate(item["components"]["field"]) if g["value"] is None]
        prior[t["task_id"]] = {"field": score(features[t["task_id"]], FEATURES, w, unknown[0] if unknown else None)}
    save(DEST / "field_prior.json", prior)
    save(DEST / "manifest.json", {"bank": "common_candidate_v30", "structural_prior_v27_sha256": json.loads((PRIOR27 / "manifest.json").read_text())["structural_prior_sha256"],
                                  "field_prior_sha256": sha(DEST / "field_prior.json"), "llm_ranking_answers": len(ranks),
                                  "fit": {"weights": dict(zip(FEATURES, w.tolist())), "final_loss": loss, "leave_one_company_out_accuracy": correct / max(1, counted),
                                          "llm_rank1_only_accuracy": llm_only / max(1, counted), "supervised_targets": counted},
                                  "features": FEATURES, "llm": "deepseek-flash zero-shot field ranking on public candidates, vision input",
                                  "supervision": "2023 historical teachers only; 2024/25 references never read", "source_sha256": sha(Path(__file__))})
    print("v31 field prior frozen", flush=True)


if __name__ == "__main__":
    main()
