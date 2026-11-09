"""v29: a common field (concept) prior for sibling-concept disambiguation.

Every field candidate of a target receives features that compare the concept's
ontology properties and labels with the visible row label (merged across
wrapped lines), the column headers, the statement title block, the structural
period prior of v27, the printed sign, and the same company's 2023 teacher
bindings (row-label memory). A per-target softmax scorer is fitted on the 2023
historical teachers only; for the 2023 fit the memory features are computed
leaving the target itself out. The centred logits are written as an additive
prior for every learner.

No reference, teacher or future file is read for 2024/25 targets.
"""

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import torch
from native_binding_prepare_v14 import DEST as TEACHERS
from native_header_alignment_v21 import DEST as ALIGN
from native_structural_prior_v27 import DEST as V27
from native_structural_prior_v27 import fit, is_numeric, score
from native_v26_bank import DEST as BANK
from native_v26_bank import text_lines
from pilot_features import encode_texts
from prepare import save, sha

DEST = ALIGN.parent / "common_candidate_v29"
KEYWORDS = ("other", "net", "total", "comprehensive", "attributable", "continuing", "discontinued", "adjusted", "current", "noncurrent", "gross", "deferred", "basic", "diluted", "parent", "owners")
STATEMENTS = {"position": ("financial position", "balance sheet"), "income": ("income statement", "profit or loss", "profit and loss", "statement of comprehensive income", "comprehensive income"),
              "cashflow": ("cash flow", "cash flows"), "equity": ("changes in equity",), "notes": ("notes to", "note ")}
FEATURES = (["retrieval_0", "retrieval_1", "retrieval_2", "retrieval_rel", "retrieval_rank_log", "retrieval_top1",
             "row_cos", "row_jaccard", "row_cos_rel", "row_jaccard_rel", "col_cos", "block_cos",
             "period_type_matches_structural", "period_type_unknown", "balance_credit_x_negative", "balance_debit_x_negative", "balance_none",
             "extension_namespace", "label_words_log", "label_longer_than_row", "abstract",
             "memory_exact_field", "memory_best_sim", "memory_best_field", "memory_any_use", "memory_use_count_log"]
            + ["kw_%s_row_only" % w for w in KEYWORDS] + ["kw_%s_label_only" % w for w in KEYWORDS]
            + ["stmt_%s_x_%s" % (s, p) for s in STATEMENTS for p in ("instant", "duration")])


def toks(text):
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in {"the", "of", "and", "a", "an", "in", "for", "to", "on", "from", "at", "by"}}


def camel(name):
    return re.sub(r"(?<!^)(?=[A-Z])", " ", name.split("}")[-1])


def norm(text):
    return re.sub(r"[^a-z0-9 ]", " ", text.lower()).strip()


def row_label(words, tb, max_lines=3):
    """Row label to the left of the target, merged with wrapped label lines directly above."""
    tx0, ty0, tx1, ty1 = tb
    cy = (ty0 + ty1) / 2
    rows = text_lines(words)
    centres = [float(np.mean([(words[i]["box"][1] + words[i]["box"][3]) / 2 for i in ids])) for ids in rows]
    same = [j for j, yc in enumerate(centres) if abs(yc - cy) <= 0.007]
    if not same:
        return ""
    j = same[0]
    left = [i for i in rows[j] if words[i]["box"][2] <= tx0 + 0.002 and not is_numeric(words[i]["text"])]
    if not left:
        return ""
    lines = [" ".join(words[i]["text"] for i in left)]
    x0 = min(words[i]["box"][0] for i in left)
    k = j - 1
    while k >= 0 and len(lines) < max_lines:
        ids = rows[k]
        gap = centres[k + 1] - centres[k]
        texts = [words[i]["text"] for i in ids]
        if gap > 0.02 or any(is_numeric(t) for t in texts) or abs(words[ids[0]]["box"][0] - x0) > 0.03 or words[ids[-1]]["box"][2] > 0.6:
            break
        lines.insert(0, " ".join(texts))
        k -= 1
    return " ".join(lines)


def statement_kind(blocks):
    text = " ".join(blocks).lower()
    return {s: float(any(p in text for p in pats)) for s, pats in STATEMENTS.items()}


def field_features(task, groups, records, context, row, vectors, structural_period_kind, negative_glyph, memory):
    labels_of = lambda m: [l for l in (m.get("labels") or []) if l] or [camel(m.get("name", "")) if m.get("name") else ""]
    rt = toks(row)
    cols = [c for c in context["column"] if c]
    blocks = [b for b in context["blocks"] if b]
    stmt = statement_kind(blocks)
    cos = lambda a, b: float(max((vectors[x] @ vectors[y] for x in a for y in b if x in vectors and y in vectors), default=0.0))
    scores = [(m.get("retrieval_scores") or [0, 0, 0]) for m in records]
    best = [max((s[i] for s in scores), default=0.0) for i in range(3)]
    order = sorted(range(len(groups)), key=lambda i: -(0.65 * scores[i][0] + 0.25 * scores[i][1] + 0.1 * scores[i][2]))
    rank = {i: r for r, i in enumerate(order)}
    rows, row_cos, row_jac = [], [], []
    for gi, (g, m) in enumerate(zip(groups, records, strict=True)):
        f = dict.fromkeys(FEATURES, 0.0)
        if g["value"] is None:
            rows.append(f); row_cos.append(0.0); row_jac.append(0.0)
            continue
        labels = labels_of(m)
        lt = set().union(*(toks(l) for l in labels))
        s = scores[gi]
        f["retrieval_0"], f["retrieval_1"], f["retrieval_2"] = s[0], s[1], s[2]
        f["retrieval_rel"] = 0.65 * (s[0] - best[0]) + 0.25 * (s[1] - best[1]) + 0.1 * (s[2] - best[2])
        f["retrieval_rank_log"] = float(np.log1p(rank[gi]))
        f["retrieval_top1"] = float(rank[gi] == 0)
        f["row_cos"] = cos(labels, [row]) if row else 0.0
        f["row_jaccard"] = len(lt & rt) / max(1, len(lt | rt)) if rt else 0.0
        f["col_cos"] = cos(labels, cols)
        f["block_cos"] = cos(labels, blocks)
        pt = m.get("period_type")
        f["period_type_matches_structural"] = float(pt == structural_period_kind) if structural_period_kind else 0.0
        f["period_type_unknown"] = float(pt not in ("instant", "duration"))
        bal = m.get("balance")
        f["balance_credit_x_negative"] = float(bal == "credit" and negative_glyph)
        f["balance_debit_x_negative"] = float(bal == "debit" and negative_glyph)
        f["balance_none"] = float(bal not in ("credit", "debit"))
        f["extension_namespace"] = float("ifrs" not in (m.get("namespace") or ""))
        f["label_words_log"] = float(np.log1p(len(lt)))
        f["label_longer_than_row"] = float(len(lt) > len(rt)) if rt else 0.0
        f["abstract"] = float(bool(m.get("abstract")))
        for w in KEYWORDS:
            f["kw_%s_row_only" % w] = float(w in rt and w not in lt)
            f["kw_%s_label_only" % w] = float(w in lt and w not in rt)
        for sname, present in stmt.items():
            if present and pt in ("instant", "duration"):
                f["stmt_%s_x_%s" % (sname, pt)] = 1.0
        mem = memory.get(g["value"])
        if mem:
            f["memory_exact_field"] = float(mem["exact"])
            f["memory_best_sim"] = mem["sim"]
            f["memory_best_field"] = float(mem["best"])
            f["memory_any_use"] = 1.0
            f["memory_use_count_log"] = float(np.log1p(mem["count"]))
        rows.append(f); row_cos.append(f["row_cos"]); row_jac.append(f["row_jaccard"])
    mc, mj = max(row_cos, default=0.0), max(row_jac, default=0.0)
    for f in rows:
        if f["label_words_log"]:
            f["row_cos_rel"], f["row_jaccard_rel"] = f["row_cos"] - mc, f["row_jaccard"] - mj
    return rows


def company_memory(entries, row, vectors, exclude=None):
    """Same-company 2023 teacher memory for one target: per field, exact / best-similarity / use counts."""
    result = {}
    nr = norm(row)
    sims = []
    for task_id, r, field in entries:
        if task_id == exclude:
            continue
        sim = float(vectors[nr] @ vectors[r]) if nr and r and nr in vectors and r in vectors else 0.0
        sims.append((sim, field, r))
        entry = result.setdefault(field, {"exact": False, "sim": 0.0, "best": False, "count": 0})
        entry["count"] += 1
        entry["exact"] = entry["exact"] or (bool(nr) and r == nr)
        entry["sim"] = max(entry["sim"], sim)
    if sims:
        best = max(sims, key=lambda x: x[0])
        if best[0] >= 0.6:
            result[best[1]]["best"] = True
    return result


def main():
    if (DEST / "manifest.json").exists():
        raise RuntimeError("v29 field prior already frozen")
    DEST.mkdir(parents=True, exist_ok=True)
    tasks = json.loads((BANK / "public/tasks.json").read_text())
    records = json.loads((BANK / "public/records.json").read_text())
    inventory = json.loads((BANK / "public/witnesses.json").read_text())
    pages = json.loads((ALIGN / "localization/public_pages.json").read_text())
    anchors = json.loads((ALIGN / "localization/public_anchors.json").read_text())
    teachers = json.loads((TEACHERS / "training_only/past_binding_feedback.json").read_text())
    contexts = json.loads((V27 / "table_contexts.json").read_text())
    structural = json.loads((V27 / "structural_prior.json").read_text())
    v27 = json.loads((V27 / "manifest.json").read_text())
    if not (len(tasks) == len(records) == len(inventory) == 840):
        raise ValueError("All 840 public targets required")
    rows_text, strings = {}, set()
    for task in tasks:
        words = pages[anchors[task["task_id"]]["page_hash"]]["words"]
        rows_text[task["task_id"]] = row_label(words, task["target_box"])
        strings.add(rows_text[task["task_id"]]); strings.add(norm(rows_text[task["task_id"]]))
        strings.update(c for c in contexts[task["task_id"]]["column"] if c); strings.update(b for b in contexts[task["task_id"]]["blocks"] if b)
    for record in records:
        for r in record["field"]:
            m = r["metadata"]
            strings.update(l for l in (m.get("labels") or []) if l)
            if not m.get("labels"):
                strings.add(camel(m.get("name", "")))
    strings.discard("")
    print("Encoding", len(strings), "label strings", flush=True)
    vectors = encode_texts(sorted(strings))
    entries = defaultdict(list)
    for task in tasks:
        if task["source_year"] == 2023 and (teachers[task["task_id"]].get("teacher_binding") or {}).get("field"):
            entries[task["company"]].append((task["task_id"], norm(rows_text[task["task_id"]]), teachers[task["task_id"]]["teacher_binding"]["field"]))

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
        memory = company_memory(entries[task["company"]], rows_text[task["task_id"]], vectors, exclude=task["task_id"] if task["source_year"] == 2023 else None)
        features[task["task_id"]] = field_features(task, groups, recs, contexts[task["task_id"]], rows_text[task["task_id"]], vectors, kind, negative_glyph(task), memory)
    historical = [(t, item) for t, item in zip(tasks, inventory, strict=True) if t["source_year"] == 2023]
    samples = {}
    for t, item in historical:
        expected = (teachers[t["task_id"]].get("teacher_binding") or {}).get("field")
        samples[t["task_id"]] = (features[t["task_id"]], [g["value"] == expected for g in item["components"]["field"]])
    companies = sorted({t["company"] for t, _ in historical})
    correct = counted = baseline = 0
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
            baseline += bool(label[int(np.argmax([r["retrieval_rel"] - 5 * (r["label_words_log"] == 0) for r in rows]))])
    w, loss = fit(list(samples.values()), FEATURES, steps=400)
    print("field LOCO accuracy %.3f (retrieval-only argmax %.3f) on %d supervised historical targets" % (correct / max(1, counted), baseline / max(1, counted), counted), flush=True)
    prior = {}
    for t, item in zip(tasks, inventory, strict=True):
        unknown = [i for i, g in enumerate(item["components"]["field"]) if g["value"] is None]
        prior[t["task_id"]] = {"field": score(features[t["task_id"]], FEATURES, w, unknown[0] if unknown else None)}
    save(DEST / "field_prior.json", prior)
    save(DEST / "row_labels.json", rows_text)
    save(DEST / "manifest.json", {"bank": "common_candidate_v26", "structural_prior_v27_sha256": v27["structural_prior_sha256"],
                                  "field_prior_sha256": sha(DEST / "field_prior.json"),
                                  "fit": {"weights": dict(zip(FEATURES, w.tolist())), "final_loss": loss, "leave_one_company_out_accuracy": correct / max(1, counted),
                                          "retrieval_only_accuracy": baseline / max(1, counted), "supervised_targets": counted},
                                  "features": FEATURES, "memory": "same-company 2023 teacher row labels, leave-one-out for the 2023 fit",
                                  "supervision": "2023 historical teachers only; 2024/25 references never read", "source_sha256": sha(Path(__file__))})
    print("v29 field prior frozen", flush=True)


if __name__ == "__main__":
    main()
