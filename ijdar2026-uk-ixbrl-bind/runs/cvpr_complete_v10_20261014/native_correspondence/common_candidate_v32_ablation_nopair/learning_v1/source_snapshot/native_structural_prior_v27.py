"""v27: structural column/row ownership prior for period and dimension candidates.

For every public target the page OCR words are read (the same words every
method already sees), the target box is located inside the table, and each
period / dimension semantic group of the frozen v26 bank receives features that
describe where its visible anchors sit relative to the target: in the column
above it, on the same row to its left, or in a block label above / below it.
A small per-target softmax scorer over those features is fitted on the 2023
historical teachers only (the training side of the protocol) and its centred
logits are written as a common additive prior for every learner.

No reference, teacher or future file is read for 2024/25 targets.
"""

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import torch
from native_binding_prepare_v14 import DEST as TEACHERS
from native_dimension_network_v17 import DEST as NETWORK
from native_header_alignment_v21 import DEST as ALIGN
from native_v26_bank import DEST as BANK
from native_v26_bank import YEAR_RE, text_lines
from pilot_features import encode_texts
from prepare import save, sha

DEST = ALIGN.parent / "common_candidate_v27"
NUMERIC_RE = re.compile(r"^[\(\-–—]?[£$€]?[\d,]+(\.\d+)?[\)%]?$|^[\(\-–—]?\(?[\d,]*\.?\d+\)?$|^[-–—]$")
UNIT_WORD_RE = re.compile(r"^(£m|\$m|€m|£'000|£000|£|\$|€|%|m|'000|000|pence|p|cents?|note|notes?)$", re.I)
PERIOD_FEATURES = ["aligned_above", "aligned_gap", "aligned_xdist", "aligned_nearest", "aligned_bare_year", "aligned_full_width",
                   "same_row_left", "left_above", "left_above_gap", "left_above_nearest", "left_below", "left_below_gap", "left_below_nearest",
                   "instant", "end_is_source_year", "end_is_prior_year", "end_is_two_back", "calendar_assumed", "has_header", "log_anchors", "any_anchor"]
DIM_FEATURES = ["empty", "empty_x_page_col", "empty_x_page_row", "empty_x_page_jaccard", "empty_x_col_is_year_unit", "col_sim", "row_sim", "block_sim", "col_jaccard", "row_jaccard",
                "col_sim_minus_page", "row_sim_minus_page", "col_jaccard_minus_page", "col_jaccard_is_page_max", "n_members", "retrieval", "retrieval_rel",
                "axis_member_hint", "axis_components_of_equity", "has_column_context", "axis_block_sim", "member_col_sim_top"]


PLACEHOLDERS = {"-", "–", "—", "=", "_", "(", ")", "nil", "n/a", "na", "*", "**", "†", "‡", "~"}


def is_numeric(text):
    """Printed amounts, dashes and the '=' glyph the OCR produces for em-dash placeholders."""
    stripped = text.replace(" ", "").strip("*†‡")
    return not stripped or bool(NUMERIC_RE.match(stripped)) or stripped.lower() in PLACEHOLDERS or all(ch in "=-–—_.()" for ch in stripped)


def row_center(words, ids):
    return float(np.mean([(words[i]["box"][1] + words[i]["box"][3]) / 2 for i in ids]))


def column_bounds(words, tb, reach=0.25, gap=0.035):
    """Left/right x-bounds of the target's table column from the numeric cells around it.

    Numeric word centres within `reach` of the target row are clustered along x;
    the target's cluster is bounded by the midpoints to its neighbouring clusters."""
    tx0, ty0, tx1, ty1 = tb
    cx, cy = (tx0 + tx1) / 2, (ty0 + ty1) / 2
    centres = sorted((w["box"][0] + w["box"][2]) / 2 for w in words
                     if abs((w["box"][1] + w["box"][3]) / 2 - cy) <= reach and is_numeric(w["text"]) and w["text"].strip() and not YEAR_RE.fullmatch(w["text"]))
    clusters = []
    for c in centres:
        if clusters and c - clusters[-1][-1] <= gap:
            clusters[-1].append(c)
        else:
            clusters.append([c])
    means = [float(np.mean(c)) for c in clusters]
    mine = min(range(len(means)), key=lambda i: abs(means[i] - cx), default=None)
    if mine is None or abs(means[mine] - cx) > 0.05:
        return tx0 - 0.06, tx1 + 0.02
    left = (means[mine - 1] + means[mine]) / 2 if mine > 0 else min(tx0 - 0.08, means[mine] - 0.08)
    right = (means[mine] + means[mine + 1]) / 2 if mine + 1 < len(means) else tx1 + 0.03
    return left, right


def table_context(words, tb):
    """Column header phrases, row label and block labels around the target box."""
    tx0, ty0, tx1, ty1 = tb
    cx, cy = (tx0 + tx1) / 2, (ty0 + ty1) / 2
    rows = text_lines(words)
    left, right = column_bounds(words, tb)

    def window(i):
        x0, _, x1, _ = words[i]["box"]
        overlap = min(x1, right) - max(x0, left)
        return (overlap > 0 and overlap >= 0.5 * (x1 - x0)) or left <= (x0 + x1) / 2 <= right
    above = sorted([(row_center(words, ids), ids) for ids in rows if row_center(words, ids) < cy - 0.004], key=lambda r: -r[0])
    column, started, consecutive = [], False, 0
    for yc, ids in above:
        inside = [i for i in ids if window(i)]
        if not inside:
            if started:
                consecutive += 1
                if consecutive >= 2:
                    break
            continue
        texts = [words[i]["text"] for i in inside]
        if all(is_numeric(t) or UNIT_WORD_RE.match(t) or YEAR_RE.fullmatch(t) for t in texts):
            if started:
                break
            continue
        started, consecutive = True, 0
        column.append(" ".join(texts))
        if len(column) >= 4 or cy - yc > 0.3:
            break
    same = [ids for ids in rows if abs(row_center(words, ids) - cy) <= 0.007]
    row_label = ""
    if same:
        left = [i for i in same[0] if words[i]["box"][2] <= tx0 + 0.002 and not is_numeric(words[i]["text"])]
        row_label = " ".join(words[i]["text"] for i in left)
    blocks = []
    for yc, ids in above:
        texts = [words[i]["text"] for i in ids]
        if words[ids[0]]["box"][0] < 0.45 and not any(is_numeric(t) for t in texts[:3]) and len(texts) >= 2:
            blocks.append(" ".join(texts))
        if len(blocks) >= 2 or cy - yc > 0.35:
            break
    col_is_year_unit = bool(column) and all(YEAR_RE.fullmatch(t) or UNIT_WORD_RE.match(t) or is_numeric(t) for t in column[0].split())
    return {"column": column[::-1], "row": row_label, "blocks": blocks, "col_is_year_unit": col_is_year_unit, "column_bounds": [left, right]}


def period_features(task, groups, records, words):
    tb = task["target_box"]
    tx0, ty0, tx1, ty1 = tb
    cx, cy = (tx0 + tx1) / 2, (ty0 + ty1) / 2
    source_year = int(task["source_year"])
    rows = []
    for g, record in zip(groups, records, strict=True):
        value = g["value"]
        f = dict.fromkeys(PERIOD_FEATURES, 0.0)
        f["aligned_gap"] = f["left_above_gap"] = f["left_below_gap"] = 1.0
        f["aligned_xdist"] = 1.0
        if value is None:
            rows.append(f)
            continue
        anchors = [(w["anchor"]["box"], w["anchor"]["basis"], False) for w in g["witnesses"] if w.get("anchor")]
        header = record["metadata"].get("header")
        if header and header.get("box"):
            anchors.append((header["box"], "header", True))
            f["has_header"] = 1.0
        f["log_anchors"] = float(np.log1p(len(anchors)))
        f["any_anchor"] = float(bool(anchors))
        for box, basis, is_header in anchors:
            ax0, ay0, ax1, ay1 = box
            axc, ayc = (ax0 + ax1) / 2, (ay0 + ay1) / 2
            width = ax1 - ax0
            if ayc < cy - 0.004 and (ax0 - 0.03 <= cx <= ax1 + 0.03 or abs(axc - cx) <= 0.06):
                gap = cy - ay1
                if width < 0.6:
                    f["aligned_above"] = 1.0
                    if gap < f["aligned_gap"]:
                        f["aligned_gap"] = gap
                        f["aligned_xdist"] = abs(axc - cx)
                        f["aligned_bare_year"] = float(basis in {"annual_interval_printed_year_x_visible_month_day", "printed_year_x_visible_month_day", "printed_year_x_visible_month_day_minus_one"})
                else:
                    f["aligned_full_width"] = 1.0
            if abs(ayc - cy) <= 0.008 and ax1 <= tx0 + 0.002:
                f["same_row_left"] = 1.0
            if ayc < cy - 0.004 and axc < 0.45 and width < 0.6 and not (ax0 - 0.03 <= cx <= ax1 + 0.03):
                f["left_above"] = 1.0
                f["left_above_gap"] = min(f["left_above_gap"], cy - ayc)
            if ayc > cy + 0.004 and axc < 0.45 and width < 0.6:
                f["left_below"] = 1.0
                f["left_below_gap"] = min(f["left_below_gap"], ayc - cy)
        f["instant"] = float(value["kind"] == "instant")
        end = int((value.get("instant") or value.get("end_date"))[:4])
        f["end_is_source_year"] = float(end == source_year)
        f["end_is_prior_year"] = float(end == source_year - 1)
        f["end_is_two_back"] = float(end == source_year - 2)
        f["calendar_assumed"] = float(bool(record["metadata"].get("calendar_convention_assumed")) or record["metadata"].get("displayed_year") is not None)
        rows.append(f)
    for key, gap in (("aligned_nearest", "aligned_gap"), ("left_above_nearest", "left_above_gap"), ("left_below_nearest", "left_below_gap")):
        best = min((r[gap] for r in rows if r[gap] < 1.0), default=None)
        for r in rows:
            r[key] = float(best is not None and r[gap] == best)
    return rows


def tokens(text):
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in {"member", "of", "the", "and", "a", "an", "in", "for", "to"}}


def member_labels(task):
    labels = {}
    for m in task["dimension_member_candidates"]:
        labels[m["field"]] = [l.replace("[member]", "").strip() for l in m.get("labels", [])] or [re.sub(r"(?<!^)(?=[A-Z])", " ", m["name"].replace("Member", ""))]
    return labels


def axis_labels(task):
    labels = {}
    for a in task.get("dimension_axis_candidates", []):
        labels[a["field"]] = [l.replace("[axis]", "").strip() for l in a.get("labels", [])] or [re.sub(r"(?<!^)(?=[A-Z])", " ", a["name"].replace("Axis", ""))]
    return labels


def axis_names(labels, value):
    return [n for d in value for n in labels.get(d["axis"], [re.sub(r"(?<!^)(?=[A-Z])", " ", d["axis"].split("}")[-1].replace("Axis", ""))])]


def member_names(labels, value):
    return [n for d in value for n in labels.get(d["member"], [re.sub(r"(?<!^)(?=[A-Z])", " ", d["member"].split("}")[-1].replace("Member", ""))])]


def dimension_features(task, groups, context, labels, vectors, membership, axes):
    cols = context["column"]
    row = [context["row"]] if context["row"] else []
    blocks = context["blocks"]
    cos = lambda a, b: float(max((vectors[x] @ vectors[y] for x in a for y in b), default=0.0))
    jac = lambda a, b: float(max((len(tokens(x) & tokens(y)) / max(1, len(tokens(x) | tokens(y))) for x in a for y in b), default=0.0))
    rows, page_col, page_row, page_jac = [], 0.0, 0.0, 0.0
    retrievals = [g["metadata"].get("retrieval_score", 0.0) if g["value"] else 0.0 for g in groups]
    best_retrieval = max(retrievals, default=0.0)
    for g, retrieval in zip(groups, retrievals, strict=True):
        f = dict.fromkeys(DIM_FEATURES, 0.0)
        value = g["value"]
        if value is None:
            rows.append(f)
            continue
        if not value:
            f["empty"] = 1.0
            f["empty_x_col_is_year_unit"] = float(context["col_is_year_unit"])
            rows.append(f)
            continue
        names = member_names(labels, value)
        f["col_sim"], f["row_sim"], f["block_sim"] = cos(names, cols), cos(names, row), cos(names, blocks)
        f["col_jaccard"], f["row_jaccard"] = jac(names, cols), jac(names, row)
        f["n_members"] = float(len(value))
        f["retrieval"] = float(retrieval)
        f["retrieval_rel"] = float(retrieval - best_retrieval)
        f["axis_member_hint"] = float(all((d["axis"], d["member"]) in membership for d in value))
        f["axis_components_of_equity"] = float(any(d["axis"].endswith("ComponentsOfEquityAxis") for d in value))
        f["has_column_context"] = float(bool(cols))
        f["axis_block_sim"] = cos(axis_names(axes, value), blocks + cols)
        f["member_col_sim_top"] = cos(names, cols[-1:])
        page_col, page_row, page_jac = max(page_col, f["col_sim"]), max(page_row, f["row_sim"]), max(page_jac, f["col_jaccard"])
        rows.append(f)
    for f in rows:
        if f["empty"]:
            f["empty_x_page_col"], f["empty_x_page_row"], f["empty_x_page_jaccard"] = page_col, page_row, page_jac
        elif f["n_members"]:
            f["col_sim_minus_page"], f["row_sim_minus_page"] = f["col_sim"] - page_col, f["row_sim"] - page_row
            f["col_jaccard_minus_page"] = f["col_jaccard"] - page_jac
            f["col_jaccard_is_page_max"] = float(page_jac > 0 and f["col_jaccard"] == page_jac)
    return rows


def meta(rows, group):
    """Record metadata of a semantic group; the explicit unknown group has none."""
    if group["value"] is None or not group["source_record_indices"] or group["source_record_indices"][0] >= len(rows):
        return {"metadata": {}}
    return rows[group["source_record_indices"][0]]


def fit(samples, names, steps=600, l2=1e-3, mode="softmax"):
    """Linear scorer over candidate feature rows; labels are teacher matches.

    softmax: per-target conditional logit (one correct candidate per target).
    binary: per-candidate logistic regression, which does not split probability
    mass across the many dimension tuples that share one member text."""
    w = torch.zeros(len(names), requires_grad=True)
    opt = torch.optim.Adam([w], lr=0.05)
    xs = [(torch.tensor([[r[n] for n in names] for r in rows], dtype=torch.float32), torch.tensor(label)) for rows, label in samples if any(label)]
    if mode == "binary":
        from sklearn.linear_model import LogisticRegression
        x = torch.cat([a for a, _ in xs]).numpy(); y = torch.cat([b for _, b in xs]).numpy()
        clf = LogisticRegression(C=1.0 / max(l2, 1e-6) / 1000, class_weight="balanced", max_iter=5000).fit(x, y)
        return torch.tensor(clf.coef_[0], dtype=torch.float32), float(clf.intercept_[0])
    for _ in range(steps):
        opt.zero_grad()
        loss = sum(-torch.log_softmax(a @ w, 0)[b].max() for a, b in xs) / len(xs) + l2 * (w ** 2).sum()
        loss.backward()
        opt.step()
    return w.detach(), float(loss.detach())


def score(rows, names, w, unknown=None):
    """Centred logits; the explicit unknown candidate (all-zero features) is held neutral at the mean."""
    x = torch.tensor([[r[n] for n in names] for r in rows], dtype=torch.float32)
    logits = x @ w
    if unknown is not None and len(logits) > 1:
        known = torch.tensor([i != unknown for i in range(len(logits))])
        logits = logits.clone()
        logits[unknown] = logits[known].mean()
    return (logits - logits.max()).tolist()


def main():
    if (DEST / "structural_prior.json").exists():
        raise RuntimeError("v27 structural prior already frozen")
    DEST.mkdir(parents=True, exist_ok=True)
    tasks = json.loads((BANK / "public/tasks.json").read_text())
    records = json.loads((BANK / "public/records.json").read_text())
    inventory = json.loads((BANK / "public/witnesses.json").read_text())
    pages = json.loads((ALIGN / "localization/public_pages.json").read_text())
    anchors = json.loads((ALIGN / "localization/public_anchors.json").read_text())
    teachers = json.loads((TEACHERS / "training_only/past_binding_feedback.json").read_text())
    graphs = json.loads((NETWORK / "public/report_graphs.json").read_text())
    if not (len(tasks) == len(records) == len(inventory) == 840):
        raise ValueError("All 840 public targets required")
    contexts, strings = {}, set()
    for task, item in zip(tasks, inventory, strict=True):
        labels, axes = member_labels(task), axis_labels(task)
        for g in item["components"]["dimensions"]:
            if g["value"]:
                strings.update(member_names(labels, g["value"]))
                strings.update(axis_names(axes, g["value"]))
        words = pages[anchors[task["task_id"]]["page_hash"]]["words"]
        contexts[task["task_id"]] = table_context(words, task["target_box"])
        strings.update(contexts[task["task_id"]]["column"]); strings.update(contexts[task["task_id"]]["blocks"])
        if contexts[task["task_id"]]["row"]:
            strings.add(contexts[task["task_id"]]["row"])
        for labels in member_labels(task).values():
            strings.update(labels)
    print("Encoding", len(strings), "header and member strings", flush=True)
    vectors = encode_texts(sorted(strings))
    features = {}
    for task, record, item in zip(tasks, records, inventory, strict=True):
        words = pages[anchors[task["task_id"]]["page_hash"]]["words"]
        groups = item["components"]
        features[task["task_id"]] = {
            "period": period_features(task, groups["period"], [meta(record["period"], g) for g in groups["period"]], words),
            "dimensions": dimension_features(task, [{"value": g["value"], "metadata": meta(record["dimensions"], g)["metadata"]} for g in groups["dimensions"]], contexts[task["task_id"]], member_labels(task), vectors,
                                             {(r["axis"], r["member"]) for r in graphs[task["report"]]["axis_member_hints"]}, axis_labels(task))}
    # historical supervision (2023 teachers only)
    def labels_for(task, groups, channel):
        binding = teachers[task["task_id"]].get("teacher_binding") or {}
        expected = binding.get(channel)
        if expected is None:
            return [False] * len(groups)
        if channel == "dimensions":
            return [g["value"] is not None and sorted(g["value"], key=lambda d: d["axis"]) == sorted(expected, key=lambda d: d["axis"]) for g in groups]
        return [g["value"] == expected for g in groups]
    historical = [(task, item) for task, item in zip(tasks, inventory, strict=True) if task["source_year"] == 2023]
    companies = sorted({t["company"] for t, _ in historical})
    report = {"companies": len(companies), "historical_targets": len(historical)}
    weights = {}
    for channel, names, mode in (("period", PERIOD_FEATURES, "softmax"), ("dimensions", DIM_FEATURES, "binary")):
        samples = {t["task_id"]: (features[t["task_id"]][channel], labels_for(t, item["components"][channel], channel)) for t, item in historical}
        # leave-one-company-out check on the training side only
        correct, counted, baseline = 0, 0, 0
        for held in companies:
            train = [samples[t["task_id"]] for t, _ in historical if t["company"] != held]
            w, _ = fit(train, names, mode=mode)
            for t, _ in historical:
                if t["company"] != held:
                    continue
                rows, label = samples[t["task_id"]]
                if not any(label):
                    continue
                counted += 1
                correct += bool(label[int(np.argmax(score(rows, names, w)))])
                baseline += bool(label[int(np.argmax([r.get("retrieval", 0.0) + r.get("aligned_nearest", 0.0) - 5 * (r.get("calendar_assumed", 0.0) == 0 and channel == "period") for r in rows]))])
        w, loss = fit(list(samples.values()), names, mode=mode)
        weights[channel] = {"weights": dict(zip(names, w.tolist())), "final_loss": loss, "fit_mode": mode,
                            "leave_one_company_out_accuracy": correct / max(1, counted), "supervised_targets": counted}
        print(channel, "LOCO accuracy %.3f on %d supervised historical targets" % (correct / max(1, counted), counted), flush=True)
    prior = {}
    for t, item in zip(tasks, inventory, strict=True):
        prior[t["task_id"]] = {}
        for c, n in (("period", PERIOD_FEATURES), ("dimensions", DIM_FEATURES)):
            unknown = [i for i, g in enumerate(item["components"][c]) if g["value"] is None]
            prior[t["task_id"]][c] = score(features[t["task_id"]][c], n, torch.tensor(list(weights[c]["weights"].values())), unknown[0] if unknown else None)
    save(DEST / "structural_prior.json", prior)
    save(DEST / "structural_features.json", {k: v for k, v in features.items()})
    save(DEST / "table_contexts.json", contexts)
    save(DEST / "manifest.json", {"bank": "common_candidate_v26", "bank_witness_inventory_sha256": sha(BANK / "public/witnesses.json"),
                                  "structural_prior_sha256": sha(DEST / "structural_prior.json"), "fit": weights, **report,
                                  "period_features": PERIOD_FEATURES, "dimension_features": DIM_FEATURES,
                                  "supervision": "2023 historical teachers only; 2024/25 references never read",
                                  "source_sha256": sha(Path(__file__))})
    print("v27 structural prior frozen", flush=True)


if __name__ == "__main__":
    main()
