"""v28: v27 structural prior plus a field x unit-quantity pair prior.

For every unit/quantity semantic group the visible unit anchors are located
relative to the target column (as v27 does for periods), the printed glyph
(parentheses / leading minus) and the group's semantic sign are recorded, and
the page-dominant scale and currency are compared with the group's. Those
features are crossed with the field candidate's declared balance and item type
(ontology properties every learner already sees) and a balanced logistic model
is fitted on the 2023 historical teachers: positives are the teacher's own
(unit, quantity) under the teacher's field, negatives the other groups under
that field. At load time the fitted model yields an additive pair prior for
every (field, unit-quantity) combination; it is common to all learners.

No reference, teacher or future file is read for 2024/25 targets.
"""

import json
import re
from collections import Counter
from pathlib import Path

import numpy as np
from native_binding_prepare_v14 import DEST as TEACHERS
from native_header_alignment_v21 import DEST as ALIGN
from native_structural_prior_v27 import DEST as V27
from native_structural_prior_v27 import column_bounds
from native_v26_bank import DEST as BANK
from prepare import save, sha

DEST = ALIGN.parent / "common_candidate_v28"
BALANCES = ("credit", "debit", "none")
TYPES = ("monetary", "shares", "perShare", "other")
KINDS = ("monetary", "shares", "pure", "other")
Q_FEATURES = ["aligned_above", "aligned_gap", "aligned_nearest", "same_row", "page_anchor", "log_anchors", "has_header", "header_full_width",
              "scale_is_page_dominant", "scale_log10", "scale_minor", "currency_is_page_dominant", "kind_monetary", "kind_shares", "kind_pure",
              "printed_negative", "semantic_negative", "sign_flipped", "zero_hypothesis", "printed_has_decimal"]
PAIR_FEATURES = [f"{b}_x_{p}_x_{s}" for b in BALANCES for p in ("pos_glyph", "neg_glyph") for s in ("pos_sign", "neg_sign")] + \
                [f"type_{t}_x_kind_{k}" for t in TYPES for k in KINDS] + ["perShare_x_minor", "perShare_x_unit_scale", "monetary_x_major_scale"]
FEATURES = Q_FEATURES + PAIR_FEATURES


def field_codes(metadata):
    balance = metadata.get("balance") if metadata.get("balance") in BALANCES else "none"
    t = (metadata.get("type") or "").split("}")[-1]
    kind = "monetary" if t == "monetaryItemType" else "shares" if t == "sharesItemType" else "perShare" if t == "perShareItemType" else "other"
    return BALANCES.index(balance), TYPES.index(kind)


def glyph_flags(task, printed):
    for q in task["quantity_candidates"]:
        if q["printed_value"] == printed:
            glyph = (q.get("source_glyph_hypotheses") or [""])[0].strip()
            return glyph.startswith("("), bool(q.get("semantic_zero_hypothesis_not_literal_zero")) or glyph in {"—", "–", "-", "="}
    return False, False


def page_dominants(task):
    scales, currencies = Counter(), Counter()
    for u in task["unit_candidates"]:
        n = len(u.get("ocr_anchors") or []) + (1 if u.get("header") else 0)
        if u.get("multiplier") is not None and u.get("kind") == "monetary":
            scales[u["multiplier"]] += n
        if u.get("unit") and u["unit"].get("numerator"):
            currencies[u["unit"]["numerator"][0]] += n
    return (scales.most_common(1) or [(None, 0)])[0][0], (currencies.most_common(1) or [(None, 0)])[0][0]


def quantity_features(task, groups, records, words):
    tb = task["target_box"]
    tx0, ty0, tx1, ty1 = tb
    cx, cy = (tx0 + tx1) / 2, (ty0 + ty1) / 2
    left, right = column_bounds(words, tb)
    scale, currency = page_dominants(task)
    rows = []
    for g, record in zip(groups, records, strict=True):
        f = dict.fromkeys(Q_FEATURES, 0.0)
        f["aligned_gap"] = 1.0
        value, m = g["value"], record.get("metadata", {})
        if value is None:
            rows.append(f)
            continue
        anchors = [(w["anchor"]["box"], False) for w in g["witnesses"] if w.get("anchor")]
        header = m.get("header")
        if header and header.get("box"):
            anchors.append((header["box"], True))
            f["has_header"] = 1.0
            f["header_full_width"] = float(header["box"][2] - header["box"][0] >= 0.6)
        f["log_anchors"] = float(np.log1p(len(anchors)))
        for (ax0, ay0, ax1, ay1), is_header in anchors:
            axc, ayc = (ax0 + ax1) / 2, (ay0 + ay1) / 2
            if ax1 - ax0 >= 0.6:
                continue
            if ayc < cy - 0.004 and (left <= axc <= right or (ax0 <= cx <= ax1)):
                f["aligned_above"] = 1.0
                f["aligned_gap"] = min(f["aligned_gap"], cy - ay1)
            elif abs(ayc - cy) <= 0.008:
                f["same_row"] = 1.0
            else:
                f["page_anchor"] = 1.0
        multiplier = m.get("multiplier")
        if multiplier is not None:
            power = float(np.log10(float(multiplier))) if float(multiplier) > 0 else 0.0
            f["scale_log10"], f["scale_minor"] = power / 6.0, float(power < 0)
            f["scale_is_page_dominant"] = float(multiplier == scale)
        f["currency_is_page_dominant"] = float(bool(value["unit"]["numerator"]) and value["unit"]["numerator"][0] == currency)
        kind = m.get("kind") or "other"
        for k in ("monetary", "shares", "pure"):
            f["kind_" + k] = float(kind == k)
        negative_glyph, zero = glyph_flags(task, m.get("printed"))
        f["printed_negative"] = float(negative_glyph)
        f["semantic_negative"] = float(str(value["quantity"]).startswith("-"))
        f["sign_flipped"] = float(negative_glyph != bool(f["semantic_negative"]))
        f["zero_hypothesis"] = float(zero)
        f["printed_has_decimal"] = float("." in (m.get("printed") or ""))
        f["_kind"], f["_power"] = kind, (float(np.log10(float(multiplier))) if multiplier is not None and float(multiplier) > 0 else 0.0)
        rows.append(f)
    best = min((r["aligned_gap"] for r in rows if r["aligned_gap"] < 1.0), default=None)
    for r in rows:
        r["aligned_nearest"] = float(best is not None and r["aligned_gap"] == best)
    return rows


def pair_vector(q, balance_index, type_index):
    """Feature vector of one (field, unit-quantity) pair from the group's features and the field's codes."""
    x = [q[n] for n in Q_FEATURES]
    b, t = BALANCES[balance_index], TYPES[type_index]
    kind = q.get("_kind", "other") if q.get("_kind") in KINDS else "other"
    glyph = "neg_glyph" if q["printed_negative"] else "pos_glyph"
    sign = "neg_sign" if q["semantic_negative"] else "pos_sign"
    known = q.get("_kind") is not None
    for name in PAIR_FEATURES:
        if name == f"{b}_x_{glyph}_x_{sign}":
            x.append(1.0 if known else 0.0)
        elif name == f"type_{t}_x_kind_{kind}":
            x.append(1.0 if known else 0.0)
        elif name == "perShare_x_minor":
            x.append(float(known and t == "perShare" and q["scale_minor"] > 0))
        elif name == "perShare_x_unit_scale":
            x.append(float(known and t == "perShare" and q.get("_power", 0.0) == 0.0))
        elif name == "monetary_x_major_scale":
            x.append(float(known and t == "monetary" and q.get("_power", 0.0) >= 3))
        else:
            x.append(0.0)
    return x


def pair_logits(qrows, weights, intercept):
    """(balance x type) x groups logit table for one target."""
    w = np.array([weights[n] for n in FEATURES], dtype="float32")
    table = np.zeros((len(BALANCES), len(TYPES), len(qrows)), dtype="float32")
    for bi in range(len(BALANCES)):
        for ti in range(len(TYPES)):
            table[bi, ti] = np.array([pair_vector(q, bi, ti) for q in qrows], dtype="float32") @ w + intercept
    return table


def main():
    if (DEST / "manifest.json").exists():
        raise RuntimeError("v28 structural prior already frozen")
    DEST.mkdir(parents=True, exist_ok=True)
    tasks = json.loads((BANK / "public/tasks.json").read_text())
    records = json.loads((BANK / "public/records.json").read_text())
    inventory = json.loads((BANK / "public/witnesses.json").read_text())
    pages = json.loads((ALIGN / "localization/public_pages.json").read_text())
    anchors = json.loads((ALIGN / "localization/public_anchors.json").read_text())
    teachers = json.loads((TEACHERS / "training_only/past_binding_feedback.json").read_text())
    v27 = json.loads((V27 / "manifest.json").read_text())
    if not (len(tasks) == len(records) == len(inventory) == 840):
        raise ValueError("All 840 public targets required")
    features, codes = {}, {}
    for task, record, item in zip(tasks, records, inventory, strict=True):
        words = pages[anchors[task["task_id"]]["page_hash"]]["words"]
        groups = item["components"]["unit_quantity"]
        rec = [record["unit_quantity"][g["source_record_indices"][0]] if g["value"] is not None and g["source_record_indices"] and g["source_record_indices"][0] < len(record["unit_quantity"]) else {"metadata": {}} for g in groups]
        features[task["task_id"]] = quantity_features(task, groups, rec, words)
        codes[task["task_id"]] = [list(field_codes(record["field"][g["source_record_indices"][0]]["metadata"])) if g["value"] is not None else [2, 3] for g in item["components"]["field"]]
    # supervision: teacher field's balance/type x each unit-quantity group
    from sklearn.linear_model import LogisticRegression
    X, Y, meta = [], [], []
    for task, record, item in zip(tasks, records, inventory, strict=True):
        if task["source_year"] != 2023:
            continue
        binding = teachers[task["task_id"]].get("teacher_binding") or {}
        if not binding.get("field") or binding.get("quantity") is None:
            continue
        fields = [g for g in item["components"]["field"] if g["value"] == binding["field"]]
        if not fields:
            continue
        bi, ti = field_codes(record["field"][fields[0]["source_record_indices"][0]]["metadata"])
        labels = []
        for g, q in zip(item["components"]["unit_quantity"], features[task["task_id"]], strict=True):
            y = g["value"] is not None and g["value"]["unit"] == binding["unit"] and float(g["value"]["quantity"]) == float(binding["quantity"])
            X.append(pair_vector(q, bi, ti)); Y.append(y); labels.append(y)
        meta.append((task["company"], any(labels)))
    X, Y = np.array(X, dtype="float32"), np.array(Y)
    clf = LogisticRegression(C=1.0, class_weight="balanced", max_iter=5000).fit(X, Y)
    weights = dict(zip(FEATURES, clf.coef_[0].tolist())); intercept = float(clf.intercept_[0])
    # leave-one-company-out argmax check under the teacher field
    companies = sorted({c for c, _ in meta}); correct = counted = 0
    offsets, pos = [], 0
    for task, record, item in zip(tasks, records, inventory, strict=True):
        if task["source_year"] != 2023:
            continue
        binding = teachers[task["task_id"]].get("teacher_binding") or {}
        if not binding.get("field") or binding.get("quantity") is None or not [g for g in item["components"]["field"] if g["value"] == binding["field"]]:
            continue
        n = len(item["components"]["unit_quantity"]); offsets.append((task["company"], pos, n)); pos += n
    for held in companies:
        keep = np.concatenate([np.arange(p, p + n) for c, p, n in offsets if c != held]) if len(companies) > 1 else np.arange(len(Y))
        w = LogisticRegression(C=1.0, class_weight="balanced", max_iter=5000).fit(X[keep], Y[keep])
        for c, p, n in offsets:
            if c != held or not Y[p:p + n].any():
                continue
            counted += 1; correct += bool(Y[p + int(np.argmax(w.decision_function(X[p:p + n])))])
    print("unit_quantity LOCO accuracy under the teacher field %.3f on %d targets" % (correct / max(1, counted), counted), flush=True)
    prior = {}
    for task in tasks:
        table = pair_logits(features[task["task_id"]], weights, intercept)
        prior[task["task_id"]] = {"pair_logits": table.tolist(), "field_codes": codes[task["task_id"]]}
    save(DEST / "unit_quantity_pair_prior.json", prior)
    save(DEST / "unit_quantity_features.json", {k: [{n: v for n, v in r.items()} for r in rows] for k, rows in features.items()})
    save(DEST / "manifest.json", {"bank": "common_candidate_v26", "structural_prior_v27_sha256": v27["structural_prior_sha256"],
                                  "unit_quantity_pair_prior_sha256": sha(DEST / "unit_quantity_pair_prior.json"),
                                  "fit": {"weights": weights, "intercept": intercept, "leave_one_company_out_accuracy": correct / max(1, counted), "supervised_targets": counted,
                                          "pairs": int(len(Y)), "positives": int(Y.sum())},
                                  "features": FEATURES, "balances": BALANCES, "types": TYPES,
                                  "supervision": "2023 historical teachers only; 2024/25 references never read",
                                  "source_sha256": sha(Path(__file__))})
    print("v28 pair prior frozen", flush=True)


if __name__ == "__main__":
    main()
