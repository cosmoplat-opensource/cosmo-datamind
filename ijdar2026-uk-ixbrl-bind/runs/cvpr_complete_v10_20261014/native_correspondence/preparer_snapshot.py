"""Pointed native correspondence inputs without field or scale answer oracles.

Original-pixel target localization is supplied equally to every control. The
publisher's field, unit, period and quantity are teacher/scorer-only. Candidate
fields are retrieved from the public schema using native OCR, never a reference.
"""

import json
import re
from pathlib import Path

import numpy as np
from crop_candidates_v3 import DEST as SOURCE
from pilot_features import TEXT_MODEL, encode_texts
from prepare import OUT, save, sha

DEST = OUT.parent / "cvpr_complete_v10_20261014/native_correspondence"
FIELDS_PER_TARGET = 32
PUBLIC_KEYS = {
    "task_id",
    "company",
    "report",
    "source_year",
    "page_id",
    "image",
    "image_sha256",
    "target_box",
    "crop_image",
    "crop_image_sha256",
    "native_ocr_strings",
    "crop_ocr_hypotheses",
    "public_row_text",
    "public_top_text",
    "field_candidates",
    "track",
}


def field_text(name, properties):
    readable = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name).replace("_", " ")
    return (
        readable
        + " "
        + " ".join(
            str(properties.get(k) or "") for k in ("type", "period_type", "balance")
        )
    )


def public_target(task, cards):
    return {
        "task_id": task["task_id"],
        "company": task["company"],
        "report": task["report"],
        "source_year": task["source_year"],
        "page_id": task["page_id"],
        "image": task["image"],
        "image_sha256": task["image_sha256"],
        "target_box": task["target_box"],
        "crop_image": task["crop_image"],
        "crop_image_sha256": task["crop_image_sha256"],
        "native_ocr_strings": task["ocr_strings"],
        "crop_ocr_hypotheses": task["crop_OCR_hypotheses"],
        "public_row_text": task["public_row_text"],
        "public_top_text": task["public_top_text"],
        "field_candidates": cards,
        "track": "supplied_pointed_region_native_correspondence_without_target_field_or_scale",
    }


def prepare():
    frozen = DEST / "protocol.json"
    if frozen.exists():
        raise RuntimeError("Direct correspondence input protocol already frozen")
    source_file = SOURCE / "public/tasks.json"
    tasks = json.loads(source_file.read_text())
    schema_file = (
        OUT / "expanded_development_sources/public_schema/ifrs_20220324_properties.json"
    )
    schema = json.loads(schema_file.read_text())
    names = sorted(schema["fields"])
    catalog = [
        dict(
            schema["fields"][name],
            field_id=f"f{i:05d}",
            name=name,
            namespace=schema["namespace"],
            readable_text=field_text(name, schema["fields"][name]),
        )
        for i, name in enumerate(names)
    ]
    # These two strings are the only target-specific retrieval input. Neither
    # the old declared field nor its type/namespace nor multiplier is included.
    queries = {
        t["task_id"]: (t["public_row_text"] + " " + t["public_top_text"][:800]).strip()
        for t in tasks
    }
    embeddings = encode_texts(
        sorted({c["readable_text"] for c in catalog} | set(queries.values()))
    )
    matrix = np.stack([embeddings[c["readable_text"]] for c in catalog])
    public = []
    for task in tasks:
        scores = matrix @ embeddings[queries[task["task_id"]]]
        chosen = np.argsort(-scores, kind="stable")[:FIELDS_PER_TARGET]
        public.append(public_target(task, [catalog[j] for j in chosen]))
    if any(set(t) != PUBLIC_KEYS for t in public) or len(
        {t["task_id"] for t in public}
    ) != len(public):
        raise RuntimeError("Native public whitelist or target identity differs")
    save(DEST / "public/tasks.json", public)
    save(DEST / "public/field_catalog.json", catalog)
    prefixes = json.loads((SOURCE / "feedback_prefixes.json").read_text())
    save(DEST / "feedback_prefixes.json", prefixes)
    # Reference records are prepared AFTER candidate retrieval. They cannot
    # influence candidate inclusion, order, source selection or image reading.
    old_refs_file = SOURCE / "scoring_only/references.json"
    old_refs = json.loads(old_refs_file.read_text())
    references, past = {}, {}
    audit_cache = {}
    for task in tasks:
        ident = task["task_id"]
        ref = old_refs[ident]
        file = Path(ref["audit_file"])
        if file not in audit_cache:
            audit_cache[file] = {
                r["region_id"]: r
                for r in json.loads(file.read_text())["rows"]
                if r.get("region_id")
            }
        fact = audit_cache[file][ref["publisher_region_id"]]
        if (
            fact["canonical_value"] is None
            or str(fact["canonical_value"]) != ref["canonical_value"]
        ):
            from decimal import Decimal

            if Decimal(str(fact["canonical_value"])) != Decimal(ref["canonical_value"]):
                raise RuntimeError("Publisher teacher identity/quantity differs")
        expected = {
            "task_id": ident,
            "source_year": task["source_year"],
            "field_name": task["field_properties"]["name"],
            "field_namespace": task["field_properties"]["namespace"],
            "canonical_value": ref["canonical_value"],
            "canonical_dimensions": fact["canonical_dimensions"],
            "publisher_region_id": ref["publisher_region_id"],
            "source_audit_sha256": sha(file),
            "visible_header_ownership_not_certified": True,
            "publisher_annotation_does_not_certify_visual_identifiability": True,
        }
        references[ident] = expected
        if task["source_year"] == 2023:
            past[ident] = expected
    save(DEST / "scoring_only/references.json", references)
    save(DEST / "training_only/past_feedback.json", past)
    protocol = {
        "targets": len(public),
        "legal_entities": len(prefixes),
        "past_2023_targets": len(past),
        "future_2024_2025_targets": len(public) - len(past),
        "pointed_localization_is_an_explicit_shared_annotation_input": True,
        "target_field_identity_supplied": False,
        "target_multiplier_supplied": False,
        "publisher_quantity_unit_period_dimensions_supplied_to_student": False,
        "old_oracle_conditioned_visual_responses_reused": False,
        "same_original_pixels_and_target_selection_as_previous_diagnostic": True,
        "candidate_field_retrieval_uses_only_native_row_and_top_OCR": True,
        "candidate_field_count": FIELDS_PER_TARGET,
        "schema_covers_only_exact_2022_namespace_not_all_extensions": True,
        "future_references_are_for_post_inference_scoring_only": True,
        "not_an_independent_new_source_test": True,
        "no_reference_availability_or_retrieval_success_filters_applied": True,
        "visual_scope_ownership_gold_not_created_from_numeric_agreement": True,
        "human_runtime_confirmations": 0,
        "automatic_feedback_budgets": [0, 5, 10],
        "public_keys": sorted(PUBLIC_KEYS),
        "hashes": {
            "public_tasks": sha(DEST / "public/tasks.json"),
            "public_catalog": sha(DEST / "public/field_catalog.json"),
            "source_public_tasks": sha(source_file),
            "public_schema": sha(schema_file),
            "original_references": sha(old_refs_file),
            "past_feedback": sha(DEST / "training_only/past_feedback.json"),
            "prefixes": sha(DEST / "feedback_prefixes.json"),
            "runner": sha(__file__),
        },
        "text_encoder_files": {
            p.name: sha(p) for p in TEXT_MODEL.iterdir() if p.is_file()
        },
    }
    save(frozen, protocol)
    (DEST / "preparer_snapshot.py").write_bytes(Path(__file__).read_bytes())
    print(
        "Frozen native pointed correspondences",
        len(public),
        "field and scale oracles removed",
        flush=True,
    )


if __name__ == "__main__":
    prepare()
