"""Prepare native-word/header coverage supervision from pinned FinTabNet.c.

The student sees unchanged table crops and publisher word boxes/text, never
annotated rows, columns, header flags, or cell extents. Coverage labels describe
annotated table topology, not financial field/unit/period semantics. Selection
uses official splits and source identities before any model outputs are read.
"""

import argparse
from collections import Counter
from hashlib import sha256
import io
import json
import math
from pathlib import Path, PurePosixPath
import re
import tarfile
import xml.etree.ElementTree as ET

from PIL import Image
from prepare import EXP, save, sha

DEST = EXP / "runs/cvpr_complete_v10_20261014/fintab_header_coverage"
ARCHIVES = {
    "annotations": (
        EXP / "ext/cvpr_scope_v10/FinTabNet.c-PDF_Annotations.tar.gz.partial",
        "bc32348eb1e73a6a8207f41267eb1b5a81e6ef5b0ff79d656f53d5f2e36ce60f",
    ),
    "structure": (
        EXP / "ext/cvpr_scope_v10/FinTabNet.c-Structure.tar.gz.partial",
        "bde6a9443e08f6c94f8cf65fcd439d516131ede49c59ed2279959a8be3d0fbf6",
    ),
}
REVISION = "e5673a90b98d02c4832f9e836d72762f0e8933a0"
COUNTS = {"train": 1000, "val": 200}
ISSUER_CAP = 25


def identity(row):
    document = row["document_id"]
    match = re.fullmatch(r"([A-Za-z0-9.-]+)_(\d{4})_page_(\d+)", document)
    index = row["document_table_index"]
    if not match or type(index) is not int or index < 0:
        raise ValueError("Unsupported publisher document/table identity")
    if row["structure_id"] != f"{document}_{index}":
        raise ValueError("Publisher table identities disagree")
    return {
        "table_id": f"{document}_table_{index}",
        "issuer_id": match[1],
        "year": int(match[2]),
        "document_id": document,
        "split": row["split"],
    }


def rank(value):
    return sha256(("fintab-header-v10|" + value).encode()).hexdigest()


def heldout_issuer(issuer):
    return int(rank("issuer|" + issuer)[:8], 16) % 5 == 0


def safe_regular(member):
    path = PurePosixPath(member.name)
    if member.issym() or member.islnk() or path.is_absolute() or ".." in path.parts:
        raise ValueError("Unsafe archive member")
    return member.isfile()


def select(rows, count, split):
    eligible = [
        r for r in rows
        if r["split"] == split
        and heldout_issuer(r["issuer_id"]) == (split == "val")
    ]
    selected, documents, issuers = [], set(), Counter()
    for row in sorted(eligible, key=lambda r: rank(r["table_id"])):
        if row["document_id"] in documents or issuers[row["issuer_id"]] >= ISSUER_CAP:
            continue
        selected.append(row)
        documents.add(row["document_id"])
        issuers[row["issuer_id"]] += 1
        if len(selected) == count:
            break
    if len(selected) != count:
        raise RuntimeError("Insufficient predeclared source identities")
    return selected


def xml_geometry(body):
    root = ET.fromstring(body)
    width, height = int(root.findtext("size/width")), int(root.findtext("size/height"))
    tables = [o for o in root.findall("object") if o.findtext("name") == "table"]
    if len(tables) != 1 or min(width, height) <= 0:
        raise ValueError("Ambiguous publisher table crop geometry")
    table = [float(tables[0].findtext("bndbox/" + k)) for k in ("xmin", "ymin", "xmax", "ymax")]
    if not box_ok(table):
        raise ValueError("Invalid table rectangle")
    return width, height, table


def box_ok(box):
    return (isinstance(box, (list, tuple)) and len(box) == 4
            and all(isinstance(x, (int, float)) and math.isfinite(x) for x in box)
            and box[0] < box[2] and box[1] < box[3])


def image_to_pdf(box, image_table, pdf_table):
    if not all(box_ok(b) for b in (box, image_table, pdf_table)):
        raise ValueError("Invalid affine table geometry")
    result = []
    for j in range(4):
        axis = j % 2
        ratio = (box[j] - image_table[axis]) / (image_table[axis + 2] - image_table[axis])
        result.append(pdf_table[axis] + ratio * (pdf_table[axis + 2] - pdf_table[axis]))
    return result


def word_owner(box, cells):
    # Tight word centers that sit on multiple cell boundaries remain unknown.
    x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    hits = [i for i, c in enumerate(cells) if box_ok(c.get("pdf_bbox"))
            and c["pdf_bbox"][0] < x < c["pdf_bbox"][2]
            and c["pdf_bbox"][1] < y < c["pdf_bbox"][3]]
    return hits[0] if len(hits) == 1 else None


def coverage(header, target):
    """Positive=body columns contained in the preceding column-header span.

    Projected row headers and partially intersecting body spans are unknown.
    This does not assert semantic ownership beyond the annotated table grid.
    """
    if (not header.get("is_column_header") or target.get("is_column_header")
            or target.get("is_projected_row_header")):
        return None
    hr, tr = header.get("row_nums", []), target.get("row_nums", [])
    hc, tc = set(header.get("column_nums", [])), set(target.get("column_nums", []))
    if not hr or not tr or not hc or not tc or max(hr) >= min(tr):
        return None
    if tc <= hc:
        return True
    if tc.isdisjoint(hc):
        return False
    return None


def prepare():
    if (DEST / "manifest.json").exists():
        raise RuntimeError("FinTabNet native-word preparation already frozen")
    for path, digest in ARCHIVES.values():
        if sha(path) != digest:
            raise RuntimeError("Publisher archive SHA-256 verification failed")
    inventory, annotations, rejected = [], {}, Counter()
    with tarfile.open(ARCHIVES["annotations"][0], "r|gz") as archive:
        for member in archive:
            if not safe_regular(member) or not member.name.endswith(".json"):
                continue
            if member.size > 8 * 1024**2:
                raise ValueError("Unexpected annotation member size")
            for table in json.load(archive.extractfile(member)):
                if table.get("split") not in COUNTS:
                    # Official test labels are not parsed into this experiment.
                    continue
                if table.get("exclude_for_structure"):
                    rejected["publisher_excluded_for_structure"] += 1
                    continue
                try:
                    row = identity(table)
                except ValueError:
                    rejected["unsupported_identity"] += 1
                    continue
                ident = row["table_id"]
                if ident in annotations:
                    raise ValueError("Duplicate publisher table identity")
                inventory.append(row)
                annotations[ident] = table
    selected = sum((select(inventory, n, split) for split, n in COUNTS.items()), [])
    selection = {
        "tables": selected,
        "counts": COUNTS,
        "ordering": "fixed SHA-256 of publisher table ID",
        "issuer_cap": ISSUER_CAP,
        "one_table_per_pdf_page": True,
        "train_official_train_nonheldout_issuers": True,
        "development_official_val_heldout_issuer_identifiers": True,
        "issuer_identifiers_are_not_audited_legal_entity_aliases": True,
        "selection_uses_model_outputs": False,
        "official_test_labels_exported_or_used": False,
        "schema_only_inspection_of_first_val_records_preceded_selection": True,
    }
    frozen = DEST / "selection.json"
    if frozen.exists() and json.loads(frozen.read_text()) != selection:
        raise RuntimeError("Frozen source selection changed")
    save(frozen, selection)
    selected_map = {r["table_id"]: r for r in selected}
    extracted = {k: {} for k in selected_map}
    (DEST / "public/images").mkdir(parents=True, exist_ok=True)
    with tarfile.open(ARCHIVES["structure"][0], "r|gz") as archive:
        for member in archive:
            if not safe_regular(member):
                continue
            path = PurePosixPath(member.name)
            if len(path.parts) != 3 or path.parts[0] != "FinTabNet.c-Structure":
                continue
            suffix, name = path.suffix, path.stem
            kind = None
            if suffix == ".jpg" and path.parts[1] == "images":
                kind = "image"
            elif suffix == ".xml" and path.parts[1] in COUNTS:
                kind = "xml"
            elif suffix == ".json" and path.parts[1] == "words" and name.endswith("_words"):
                kind, name = "words", name[:-6]
            if name not in selected_map or kind is None:
                continue
            if member.size > 8 * 1024**2 or kind in extracted[name]:
                raise ValueError("Unexpected or duplicate selected archive member")
            body = archive.extractfile(member).read()
            if kind == "image":
                image = DEST / "public/images" / (name + ".jpg")
                with Image.open(io.BytesIO(body)) as im:
                    im.verify()
                image.write_bytes(body)
                extracted[name][kind] = image
            else:
                extracted[name][kind] = body
    public, labels, failures = [], {}, []
    for source in selected:
        ident = source["table_id"]
        parts = extracted[ident]
        if set(parts) != {"image", "words", "xml"}:
            failures.append({"table_id": ident, "reason": "publisher_asset_missing", "available": sorted(parts)})
            continue
        width, height, table_box = xml_geometry(parts["xml"])
        with Image.open(parts["image"]) as im:
            if im.size != (width, height):
                raise ValueError("Native image and publisher geometry differ")
        annotation = annotations[ident]
        words = json.loads(parts["words"])
        tokens, owners = [], []
        for word in words:
            if not isinstance(word.get("text"), str) or not box_ok(word.get("bbox")):
                raise ValueError("Invalid publisher native-word record")
            box = word["bbox"]
            tokens.append({"text": word["text"], "box": [box[0]/width, box[1]/height, box[2]/width, box[3]/height]})
            owners.append(word_owner(image_to_pdf(box, table_box, annotation["pdf_table_bbox"]), annotation["cells"]))
        public.append(source | {"image": str(parts["image"]), "image_sha256": sha(parts["image"]),
                                "width": width, "height": height, "native_words": tokens})
        labels[ident] = {
            "split": source["split"], "word_cell_ids": owners,
            "cells": [{k: c.get(k) for k in ("row_nums", "column_nums", "is_column_header", "is_projected_row_header")}
                      for c in annotation["cells"]],
            "unmapped_words": sum(v is None for v in owners),
            "annotated_grid_coverage_only": True,
            "financial_semantic_ownership_certified": False,
        }
    if failures:
        save(DEST / "asset_failures.json", failures)
        raise RuntimeError("Selected source assets missing; no replacement by model performance")
    save(DEST / "public/tables.json", public)
    for split in COUNTS:
        save(DEST / ("training_only/train_labels.json" if split == "train" else "scoring_only/val_labels.json"),
             {k: v for k, v in labels.items() if v["split"] == split})
    snapshot = DEST / "preparer_snapshot.py"
    snapshot.write_bytes(Path(__file__).read_bytes())
    save(DEST / "manifest.json", {
        "complete": True, "revision": REVISION,
        "source": "https://huggingface.co/datasets/bsmock/FinTabNet.c",
        "archive_sha256": {k: h for k, (_, h) in ARCHIVES.items()},
        "selected_tables": len(selected), "public_tables": len(public),
        "split_counts": dict(Counter(t["split"] for t in public)),
        "native_words": sum(len(t["native_words"]) for t in public),
        "unmapped_words_retained": sum(v["unmapped_words"] for v in labels.values()),
        "publisher_exclusion_counts": dict(rejected),
        "word_geometry_and_text_shared_by_all_controls": True,
        "annotated_cell_extents_or_header_roles_given_to_student": False,
        "table_localization_is_publisher_assisted": True,
        "header_coverage_is_grid_structure_not_financial_semantic_scope": True,
        "official_test_not_exported": True, "human_runtime_confirmations": 0,
        "hashes": {name: sha(DEST / name) for name in (
            "selection.json", "public/tables.json", "training_only/train_labels.json",
            "scoring_only/val_labels.json", "preparer_snapshot.py")},
    })
    print("Prepared native FinTabNet table/header inputs", len(public), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.parse_args()
    prepare()
