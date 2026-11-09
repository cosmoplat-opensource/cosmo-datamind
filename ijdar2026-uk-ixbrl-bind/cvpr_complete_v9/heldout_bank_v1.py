"""Held-out companies, step E: the v30-style common candidate bank for the held-out targets.

Reuses the frozen v26/v30 candidate functions (field retrieval over the report's public pool,
whole-page OCR period and unit banks, filing-metadata periods, box-glyph quantities, dash
zeros, semantic witness groups and the exact node feature layout) so the held-out packs are
shaped like the development packs. Differences, all documented: there is no DeepSeek page
observation, so the retrieval query and table context come from OCR (row label, column
headers, statement titles); no header hypotheses exist, so every witness is an OCR anchor;
dimension tuples come from the report's public definition-network hints and their pairwise
combinations plus member retrieval. Scoring references are never read here; the 2023
targets' teacher bindings are written separately for the learner's historical masks.
"""

import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import native_v26_bank as v26
import native_v30_bank as v30
if os.environ.get("HELDOUT_SOURCES"):  # sealed/other roots keep their filings elsewhere; the frozen module is not edited
    v30.SOURCES = Path(os.environ["HELDOUT_SOURCES"])
from native_binding_features_v17 import CHANNELS, candidate_records, extras
from native_field_retrieval_v12 import field_text, report_card
from native_header_alignment_v21 import DEST as ALIGN
from native_structural_prior_v27 import table_context
from native_structural_prior_v29 import row_label
from native_visible_periods_v17 import date_mentions
from native_witness_features_v23 import anchor_vector, witness_node
from pilot_features import encode_texts, pool
from prepare import opaque, save, sha

HELD = Path(os.environ.get("HELDOUT_ROOT", str(Path(__file__).resolve().parents[1] / "runs/cvpr_complete_v10_20261014/native_correspondence/heldout_v1")))
DEST = HELD / "bank"
ISO, XI = "{http://www.xbrl.org/2003/iso4217}", "{http://www.xbrl.org/2003/instance}"
ENTITY_SCHEME = "http://standards.iso.org/iso/17442"


def cos(vectors, a, b):
    return float(max((vectors[x] @ vectors[y] for x in a for y in b if x in vectors and y in vectors), default=0.0))


def dimension_lists(task, graph, catalog_by_key, report, context, vectors):
    hints = graph["axis_member_hints"]
    cols = [c for c in context["column"] if c] + ([context["row"]] if context["row"] else [])
    members, axes = {}, {}
    for h in hints:
        for key, store in ((h["member"], members), (h["axis"], axes)):
            ns, name = key[1:].split("}", 1)
            card = catalog_by_key.get((ns, name))
            if card is None:
                continue
            labels = sorted(set(card.get("labels_by_report", {}).get(report, [])) | set(card.get("labels", [])))
            store.setdefault(key, {"name": name, "namespace": ns, "field_id": card["field_id"], "labels": labels, "type": card.get("type"), "balance": card.get("balance"),
                                   "period_type": card.get("period_type"), "abstract": card.get("abstract"), "public_declaration_conflict": False, "field": key,
                                   "substitution_group": XI + "item", "declaration_role": "domain_or_member" if store is members else "axis", "typed_domain_ref": None,
                                   "schema_id": card["declaration_variants"][0]["declaration"].get("schema_id"), "axis_member_membership_certified": False})
    for m in members.values():
        names = m["labels"] or [re.sub(r"(?<!^)(?=[A-Z])", " ", m["name"].replace("Member", ""))]
        m["retrieval_score"] = cos(vectors, names, cols)
    tuples = [{"dimensions": [], "retrieval_score": 0.0, "absence_not_certified": True, "candidate_id": opaque(["heldout-dims", task["task_id"], "none"]),
               "axis_member_membership_certified": False, "visible_dimension_binding_certified": False}]
    singles = []
    for h in hints:
        score = members.get(h["member"], {}).get("retrieval_score", 0.0)
        singles.append(([{"axis": h["axis"], "kind": "explicit", "member": h["member"]}], score))
    for dims, score in singles:
        tuples.append({"dimensions": dims, "retrieval_score": score, "basis": "source_public_definition_path_hint", "candidate_id": opaque(["heldout-dims", task["task_id"], dims]),
                       "axis_member_membership_certified": False, "visible_dimension_binding_certified": False})
    for i, (a, sa) in enumerate(singles):
        for b, sb in singles[i + 1:]:
            if a[0]["axis"] == b[0]["axis"]:
                continue
            dims = sorted(a + b, key=lambda d: d["axis"])
            tuples.append({"dimensions": dims, "retrieval_score": (sa + sb) / 2, "basis": "source_public_definition_path_hint", "candidate_id": opaque(["heldout-dims", task["task_id"], dims]),
                           "axis_member_membership_certified": False, "visible_dimension_binding_certified": False})
    return tuples[:200], sorted(members.values(), key=lambda m: -m["retrieval_score"]), list(axes.values())


def main():
    if (DEST / "features/manifest.json").exists():
        raise RuntimeError("heldout bank already frozen")
    tasks = json.loads((HELD / "public/tasks.json").read_text())
    pages = json.loads((HELD / "localization/public_pages.json").read_text())
    word_index = json.loads((HELD / "native_word_features/index.json").read_text())
    schema = HELD / "schema"
    catalog = json.loads((schema / "field_catalog.json").read_text())
    namespaces = json.loads((schema / "report_namespaces.json").read_text())
    graphs = json.loads((schema / "public/report_graphs.json").read_text())
    emb = np.load(schema / "shared_embeddings/catalog.npz")["features"]
    strings = json.loads((schema / "shared_embeddings/catalog_metadata.json").read_text())["strings"]
    vectors = {s: emb[i] for i, s in enumerate(strings)}
    catalog_by_key = {(c["namespace"], c["name"]): c for c in catalog}
    pools = {}
    for report in sorted({t["report"] for t in tasks}):
        ns = set(namespaces[report])
        pools[report] = [c for c in (report_card(f, report) for f in catalog if f["namespace"] in ns) if c and v26.numeric_card(c)]
    page_text = {h: v30.PageText(p["words"]) for h, p in pages.items()}
    report_pages = defaultdict(set)
    for t in tasks:
        report_pages[t["report"]].add(t["page_hash"])
    report_month_days = {}
    for report, hashes in report_pages.items():
        md = set()
        for h in hashes:
            for text, _ in page_text[h].items:
                md.update((m["date"].month, m["date"].day) for m in date_mentions(text))
        report_month_days[report] = md
    # OCR-derived table context and retrieval queries
    public, contexts_text = {}, {}
    for t in tasks:
        words = pages[t["page_hash"]]["words"]
        ctx = table_context(words, t["target_box"])
        row = row_label(words, t["target_box"]) or ctx["row"]
        v30.PAGE_WORDS[t["task_id"]] = words
        v30.CONTEXTS[t["task_id"]] = ctx
        public[t["task_id"]] = {"task_id": t["task_id"], "public_row_text": row, "common_observation_failed": False,
                                "common_visual_observation": {"row_text": row or "unknown", "headers": [{"kind": "dimension", "text": c} for c in ctx["column"] if c],
                                                              "section_text": " | ".join(b for b in ctx["blocks"] if b) or "unknown",
                                                              "entity_text": "unknown", "dimension_text": "unknown", "version_text": "unknown"}}
        contexts_text[t["task_id"]] = (row + " | " + " | ".join(ctx["column"]) + " | " + " | ".join(ctx["blocks"]))[:3500]
    queries = {tid: v30.query_text(public[tid], public[tid]["common_visual_observation"]) for tid in public}
    dim_strings = set()
    for t in tasks:
        ctx = v30.CONTEXTS[t["task_id"]]
        dim_strings.update(c for c in ctx["column"] if c); dim_strings.add(ctx["row"] or "")
        for h in graphs[t["report"]]["axis_member_hints"]:
            ns, name = h["member"][1:].split("}", 1)
            card = catalog_by_key.get((ns, name))
            if card:
                dim_strings.update(card.get("labels_by_report", {}).get(t["report"], [])); dim_strings.update(card.get("labels", []))
                dim_strings.add(re.sub(r"(?<!^)(?=[A-Z])", " ", name.replace("Member", "")))
    dim_strings.discard("")
    print("Encoding queries and dimension strings", flush=True)
    qvec = encode_texts(sorted({s for q in queries.values() for s in q.values()}))
    dvec = encode_texts(sorted(dim_strings))
    expanded, counts = [], Counter()
    for t in tasks:
        seed_task = {**t, "field_candidates": [], "period_candidates": [], "unit_candidates": [], "quantity_candidates": [],
                     "literal_parse_candidate_count_before_cap": 0, "literal_parse_candidates_omitted_by_fixed_cap": 0, "unknown_candidates": 0,
                     "entity_visible_text": "unknown", "dimension_visible_text": "unknown", "version_visible_text": "unknown",
                     "canonical_entity_and_dimension_resolution_complete": False, "common_observation_failed": False,
                     "entity_candidates": [{"candidate_id": "heldout-entity-" + t["company"], "entity": {"scheme": ENTITY_SCHEME, "identifier": t["company"]}, "basis": "common_report_index_lei", "entity_visible_text": "unknown", "visible_text_to_entity_mapping_certified": False, "common_metadata_assistance_not_visual_identity_learning": True}],
                     "dimension_tuple_candidates": [], "dimension_member_candidates": [], "dimension_axis_candidates": [],
                     "public_row_text": public[t["task_id"]]["public_row_text"]}
        new = v30.expand_task(seed_task, public[t["task_id"]], page_text[t["page_hash"]], report_month_days[t["report"]], pools[t["report"]], vectors, qvec)
        if not any(u.get("kind") == "monetary" for u in new["unit_candidates"]):
            for power in (0, 3, 6):
                new["unit_candidates"].append({"candidate_id": opaque(["heldout-unit-fallback", t["task_id"], power]), "unit": {"numerator": [ISO + "GBP"], "denominator": []},
                                               "multiplier": v26.multiplier_text(power), "kind": "monetary", "header": None, "uncertain": True, "header_ownership_certified": False,
                                               "basis": "fallback_reporting_currency_hypothesis", "ocr_anchors": []})
        tuples, members, axes = dimension_lists(t, graphs[t["report"]], catalog_by_key, t["report"], v30.CONTEXTS[t["task_id"]], dvec)
        new.update(dimension_tuple_candidates=tuples, dimension_member_candidates=members, dimension_axis_candidates=axes)
        expanded.append(new)
        for k, key in (("fields", "field_candidates"), ("periods", "period_candidates"), ("units", "unit_candidates"), ("dims", "dimension_tuple_candidates"), ("quantities", "quantity_candidates")):
            counts[k] += len(new[key])
    (DEST / "public").mkdir(parents=True, exist_ok=True)
    save(DEST / "public/tasks.json", expanded)
    print("Building records and witnesses", flush=True)
    records = [candidate_records(t) for t in expanded]
    inventory = []
    for t, row in zip(expanded, records, strict=True):
        groups = {c: v26.build_groups(t, row[c], {}, c) for c in CHANNELS}
        inventory.append({"task_id": t["task_id"], "company": t["company"], "source_year": t["source_year"], "components": groups, "visible_scope_certified": False})
    save(DEST / "public/records.json", [{c: [{"value": r["value"], "metadata": r["metadata"]} for r in row[c]] for c in CHANNELS} for row in records])
    save(DEST / "public/witnesses.json", inventory)
    print("Encoding texts", flush=True)
    texts = sorted({r["text"] for row in records for c in CHANNELS if c != "field" for r in row[c]} | set(contexts_text.values()))
    embeddings = encode_texts(texts)
    projection = np.load(ALIGN / "features/projection.npy")
    vision_projection = np.load(ALIGN.parent / "witness_composition_v23/features/vision_projection.npy")
    folder = DEST / "features"
    folder.mkdir(parents=True, exist_ok=True)
    np.save(folder / "text_projection.npy", projection); np.save(folder / "vision_projection.npy", vision_projection)
    contexts, fcounts = [], Counter()
    cached_pages, cached_patches = {}, {}
    for number, (t, row, item) in enumerate(zip(expanded, records, inventory, strict=True), 1):
        h = t["page_hash"]
        if h not in cached_pages:
            cached_pages[h] = np.load(word_index[h]["feature_file"])
            cached_patches[h] = np.load(HELD / "native_word_features/patches" / (h + ".npy"))
        words = cached_pages[h]
        visual = pool(cached_patches[h], [min(1, max(0, v)) for v in t["target_box"]])
        visual = visual / max(float(np.linalg.norm(visual)), 1e-8)
        contexts.append(np.concatenate((embeddings[contexts_text[t["task_id"]]], visual, np.asarray(t["target_box"], dtype="float32"))).astype("float32"))
        arrays = {"common_header_pool": np.stack([anchor_vector(None, "unknown", t["target_box"], words, vision_projection, projection, unresolved=True)])}
        for c in CHANNELS:
            vecs = []
            for r in row[c]:
                v = extras(r, c, t["target_box"])
                if c == "field":
                    v[:401] = v26.field_card_vector(r["metadata"], vectors[field_text(r["metadata"])])
                else:
                    v[:384] = embeddings[r["text"]]
                if c == "unit_quantity":
                    v[436] = r["metadata"].get("semantic_zero_hypothesis_not_literal_zero", False)
                vecs.append(v)
            null = np.zeros(448, dtype="float32"); null[415] = 1; null[416 + CHANNELS.index(c)] = 1
            full = np.stack(vecs + [null])
            base = np.concatenate((full[:, :384] @ projection, full[:, 384:]), 1).astype("float32")
            groups = item["components"][c]
            if c == "field":
                arrays["field"] = np.stack([base[g["source_record_indices"][0]] for g in groups]); fcounts["field_nodes"] += len(groups); continue
            nodes, gids = [], []
            for g in groups:
                for w in g["witnesses"]:
                    kind = c if c != "unit_quantity" else "unit"
                    enc = anchor_vector(w["anchor"], kind, t["target_box"], words, vision_projection, projection, w["ownership"] == "unknown")
                    nodes.append(witness_node(base, w, g, c, t["target_box"], enc, row[c])); gids.append(g["semantic_group"])
            arrays[c] = np.stack(nodes); arrays[c + "_groups"] = np.array(gids, dtype="int64"); fcounts[c + "_witness_nodes"] += len(nodes)
        if any(not np.isfinite(v).all() for v in arrays.values()):
            raise ValueError("Nonfinite heldout features")
        np.savez_compressed(folder / (t["task_id"] + ".npz"), **arrays)
        if number % 50 == 0:
            print("heldout features", number, "/", len(expanded), flush=True)
    np.save(folder / "context.npy", np.stack(contexts)); save(folder / "ids.json", [t["task_id"] for t in expanded])
    refs = json.loads((HELD / "scoring_only/references.json").read_text())
    teachers = {t["task_id"]: {"task_id": t["task_id"], "source_year": 2023, "teacher_binding": refs[t["task_id"]], "heldout_company_own_history": True}
                for t in expanded if t["source_year"] == 2023}
    save(HELD / "training_only/past_binding_feedback.json", teachers)
    save(DEST / "public/manifest.json", {"complete": True, "prepared_utc": datetime.now(timezone.utc).isoformat(), "targets": len(expanded),
                                         "mean_candidates": {k: v / len(expanded) for k, v in counts.items()},
                                         "public_task_sha256": sha(DEST / "public/tasks.json"), "public_record_sha256": sha(DEST / "public/records.json"),
                                         "witness_inventory_sha256": sha(DEST / "public/witnesses.json"), "builder_sha256": sha(Path(__file__)),
                                         "deviations_from_development": ["OCR table context replaces the DeepSeek page observation in retrieval queries",
                                                                         "no header hypotheses; all witnesses are OCR anchors",
                                                                         "dimension tuples from definition-network hints and pairwise combinations"],
                                         "reference_files_read_for_candidates": False})
    save(folder / "manifest.json", {"complete": True, "files": {p.name: sha(p) for p in sorted(folder.glob("*.npz"))}, "witness_inventory_sha256": sha(DEST / "public/witnesses.json"),
                                    "counts": dict(fcounts), "context_sha256": sha(folder / "context.npy")})
    print("heldout bank complete", dict(counts), dict(fcounts), flush=True)


if __name__ == "__main__":
    main()
