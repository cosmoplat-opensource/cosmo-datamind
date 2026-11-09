"""Same visible context and candidate vectors for complete semantic-tuple heads."""

from collections import Counter
from datetime import date
from decimal import Decimal, localcontext
import json
from pathlib import Path

import numpy as np
import torch

from native_binding_expand_v17 import DEST
from native_field_retrieval_v12 import DEST as FIELD, field_text
from native_field_features_v12 import context_text
from pilot_features import encode_texts
from prepare import save, sha


CHANNELS = ("field", "period", "dimensions", "unit_quantity")
DIMENSION = 448


def quantity_value(printed, multiplier, sign):
    value, scale = Decimal(printed), Decimal(multiplier)
    if not value.is_finite() or not scale.is_finite() or sign not in (-1, 1):
        raise ValueError("Invalid public literal transformation")
    with localcontext() as arithmetic:
        arithmetic.prec = max(64, len(value.as_tuple().digits) + len(scale.as_tuple().digits) + 2)
        return str(value*scale*sign)


def candidate_records(task):
    fields = [{"value": "{"+c["namespace"]+"}"+c["name"], "text": field_text(c), "metadata": c}
              for c in task["field_candidates"]]
    periods = []
    for item in task["period_candidates"]:
        period = item["period"]
        value = {k: v for k, v in period.items() if k != "complete"} if period.get("complete") else None
        evidence = item.get("supporting_visible_evidence", [])
        text = " | ".join(str(v) for v in period.values()) + " | " + " | ".join(e["text"] for e in evidence[:2])
        if item.get("header"):
            text += " | " + item["header"]["text"]
        periods.append({"value": value, "text": text[:2000], "metadata": item})
    dimensions = []
    for item in task["dimension_tuple_candidates"]:
        text = " | ".join(d["axis"].split("}")[-1]+" "+d["member"].split("}")[-1] for d in item["dimensions"])
        dimensions.append({"value": item["dimensions"], "text": text or "No explicit dimension hypothesis", "metadata": item})
    quantities, seen = [], set()
    for q in task["quantity_candidates"]:
        for u in task["unit_candidates"]:
            if u.get("unit") is None or u.get("multiplier") is None:
                continue
            for sign in q["semantic_sign_candidates"]:
                value = {"unit": u["unit"], "quantity": quantity_value(q["printed_value"], u["multiplier"], sign)}
                key = json.dumps(value, sort_keys=True)
                if key in seen:
                    continue
                seen.add(key)
                text = " | ".join([q["printed_value"], u["kind"], str(u["multiplier"]), json.dumps(u["unit"]),
                                   u["header"]["text"] if u.get("header") else "unknown visible unit"])
                quantities.append({"value": value, "text": text, "metadata": {**u, "printed": q["printed_value"], "semantic_sign": sign}})
    return {"field": fields, "period": periods, "dimensions": dimensions, "unit_quantity": quantities}


def full_context(task):
    obs = task.get("common_visual_observation") or {}
    return (context_text(task) + " | " + " | ".join(obs.get(k, "unknown") for k in ("entity_text", "dimension_text", "version_text")))[:3500]


def extras(row, channel, target_box):
    vector = np.zeros(DIMENSION, dtype="float32")
    vector[416+CHANNELS.index(channel)] = 1
    metadata = row["metadata"]
    vector[412] = bool(metadata.get("uncertain", True))
    vector[414] = row["value"] is not None
    header = metadata.get("header") or {}
    if header.get("box"):
        vector[420:424] = header["box"]
        vector[424:428] = np.array(header["box"])-np.array(target_box)
    if channel == "period":
        period = metadata["period"]
        vector[405] = period["kind"] == "instant"
        vector[406] = period["kind"] == "duration"
        vector[413] = bool(metadata.get("calendar_convention_assumed"))
        end = period.get("instant") or period.get("end_date")
        if end:
            vector[408] = (date.fromisoformat(end).year-2000)/50
        if period.get("start_date") and period.get("end_date"):
            vector[409] = (date.fromisoformat(period["end_date"])-date.fromisoformat(period["start_date"])).days/366
    if channel == "dimensions":
        vector[410] = len(metadata["dimensions"])/2
        vector[411] = metadata.get("basis") == "source_public_definition_path_hint"
        vector[435] = metadata["retrieval_score"]
    if channel == "unit_quantity":
        printed = Decimal(metadata["printed"])
        vector[401] = np.sign(float(printed))*float(abs(printed).log10())/20 if printed else 0
        vector[402] = int(printed < 0)
        vector[403] = float(Decimal(metadata["multiplier"]).log10())/9
        vector[404] = metadata["semantic_sign"]
        for i, kind in enumerate(("monetary", "currency_per_share", "rate", "count", "shares")):
            vector[430+i] = metadata["kind"] == kind
    return vector


def prepare():
    folder = DEST / "features"
    if (folder / "manifest.json").exists():
        raise RuntimeError("Complete binding feature inventory already frozen")
    torch.set_num_threads(2)
    public_file = DEST / "public/tasks.json"
    manifest = json.loads((DEST / "manifest.json").read_text())
    native_file = FIELD / "public/tasks.json"
    native_manifest = json.loads((FIELD / "manifest.json").read_text())
    field_feature_file = FIELD / "features/manifest.json"
    field_features = json.loads(field_feature_file.read_text())
    if not manifest["complete"] or sha(public_file) != manifest["candidate_inventory_sha256"] or sha(native_file) != native_manifest["public_target_sha256"]:
        raise ValueError("Frozen public inputs changed")
    if any(sha(FIELD/"features"/p) != digest for p, digest in field_features["files"].items()):
        raise ValueError("Frozen shared native pixel/field features changed")
    tasks = json.loads(public_file.read_text())
    native = json.loads(native_file.read_text())
    ids = json.loads((FIELD / "features/ids.json").read_text())
    if ids != [t["task_id"] for t in tasks] or ids != [t["task_id"] for t in native]:
        raise ValueError("Full common feature order differs")
    records = [candidate_records(task) for task in tasks]
    texts = sorted({full_context(t) for t in native} | {r["text"] for rows in records for group in rows.values() for r in group})
    embeddings = encode_texts(texts)
    old_contexts = np.load(FIELD / "features/context.npy")
    old_fields = np.load(FIELD / "features/candidates.npz")
    contexts, counts = [], Counter()
    folder.mkdir(parents=True, exist_ok=True)
    for i, (task, source, components) in enumerate(zip(tasks, native, records, strict=True)):
        if task["company"] != source["company"] or task["field_candidates"] != source["field_candidates"]:
            raise ValueError("Shared metadata/field inventory changed")
        contexts.append(np.concatenate((embeddings[full_context(source)], old_contexts[i, 384:])).astype("float32"))
        vectors = {}
        for channel, rows in components.items():
            matrix = []
            for j, row in enumerate(rows):
                vector = extras(row, channel, task["target_box"])
                vector[:384] = embeddings[row["text"]]
                if channel == "field":
                    vector[:401] = old_fields[task["task_id"]][j]
                matrix.append(vector)
            null = np.zeros(DIMENSION, dtype="float32")
            null[415] = 1
            null[416+CHANNELS.index(channel)] = 1
            matrix.append(null)
            vectors[channel] = np.stack(matrix)
            counts[channel+"_candidate_nodes_including_unknown"] += len(matrix)
        np.savez_compressed(folder / (task["task_id"]+".npz"), **vectors)
    np.save(folder / "context.npy", np.stack(contexts))
    save(folder / "records.json", [{c: [{"value": r["value"], "metadata": r["metadata"]} for r in rows[c]] for c in CHANNELS} for rows in records])
    save(folder / "ids.json", ids)
    snapshot = folder / "feature_builder_snapshot.py"
    snapshot.write_bytes(Path(__file__).read_bytes())
    save(folder / "manifest.json", {
        "complete": True, "targets": len(tasks), "candidate_dimension": DIMENSION, "context_dimension": len(contexts[0]),
        "channels": list(CHANNELS), "counts": dict(counts), "public_candidate_sha256": sha(public_file),
        "original_native_public_sha256": sha(native_file), "original_field_feature_manifest_sha256": sha(field_feature_file),
        "files": {p.name: sha(p) for p in folder.iterdir() if p.is_file() and p.name != "manifest.json"},
        "entity_is_common_lei_metadata_assistance_not_a_learned_channel": True,
        "all_models_receive_identical_visible_context_pixels_geometry_and_public_schema_features": True,
        "administrative_source_year_or_company_not_appended_as_features": True,
        "visible_dates_and_entity_names_can_appear_in_shared_text_features": True,
        "teacher_or_future_reference_files_read": False, "header_ownership_certified": False,
    })
    print("Complete semantic-tuple candidate features prepared", dict(counts), flush=True)


if __name__ == "__main__":
    prepare()
