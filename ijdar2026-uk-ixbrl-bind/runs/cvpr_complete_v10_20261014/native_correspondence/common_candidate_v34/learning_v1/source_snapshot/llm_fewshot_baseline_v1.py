"""Matched-information LLM baseline: few-shot with the company's own history, conventions and filing metadata.

Addresses the review finding that the zero-shot baselines received strictly less information than
the system at budget 10. For every target the model receives, in addition to the zero-shot inputs
(table crop with the cell marked, OCR row/column/title context, the identical public candidate lists):
  * the same ten 2023 cells of the same company that the system's budget-10 configuration uses as
    automatic feedback (hash-prefix order), each described by its OCR context, printed glyph and the
    filed tuple (concept, period, dimensions, unit x multiplier, signed value);
  * a one-paragraph statement of iXBRL sign and scale conventions;
  * the filing's period end and report year.
It answers by candidate index exactly as the zero-shot baselines do. Development and held-out sets.
"""

import argparse
import base64
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import httpx
from openai import OpenAI
import llm_baseline_v30 as L
from native_baseline import MODEL, balance
from native_header_alignment_v21 import DEST as ALIGN
from native_structural_prior_v27 import table_context
from native_v30_bank import DEST as BANK
from native_v30_bank import PERCEPTION
from prepare import save, sha
from score_native_binding_v17 import component_correct, scoring_references

HELD = Path(__file__).resolve().parents[1] / "runs/cvpr_complete_v10_20261014/native_correspondence/heldout_v1"
TEACHERS = Path(__file__).resolve().parents[1] / "runs/cvpr_complete_v10_20261014/native_correspondence/full_binding_preparation_v14/training_only/past_binding_feedback.json"
SOURCES = Path(__file__).resolve().parents[1] / "ext/xbrl_scope_additional_development"
CONVENTIONS = """iXBRL conventions you must apply: (1) The filed value is the printed number times the table's scale (£m -> 1000000, £'000 -> 1000, pence per share -> 0.01 GBP). (2) Parentheses show a display negative, but the filed sign follows the concept's balance: liabilities, expenses, outflows and deductions printed in parentheses are usually filed POSITIVE under their own concept, while a 'loss' or 'decrease' concept filed from a positive print may be negative; use the examples from the same company to learn its convention. (3) Comparative columns carry the prior year's period; a statement of changes in equity has periods per row block and equity components per column. (4) Opening balances 'At 1 January 2024' are filed as the instant 2023-12-31."""


def hash_order(ids):
    return sorted(ids, key=lambda t: hashlib.sha256(t.encode()).hexdigest())


def filing_meta(report):
    lei, year = report.rsplit("_", 1)
    m = sorted(p for p in SOURCES.glob(lei + "_" + year + "*") if (p / "original.xhtml").exists())
    return {"report_year": int(year), "period_end": m[0].name.rsplit("_", 1)[1] if m else None}


def describe(task, ctx, binding, member_labels):
    dims = "; ".join("%s = %s" % (d["axis"].split("}")[-1].replace("Axis", ""), member_labels.get(d["member"], d["member"].split("}")[-1])) for d in binding.get("dimensions") or []) or "none"
    p = binding["period"]; period = p.get("instant") or (p["start_date"] + " .. " + p["end_date"])
    unit = "/".join(u.split("}")[-1] for u in binding["unit"]["numerator"]) or "pure"
    glyphs = sorted({g for q in task["quantity_candidates"] for g in q.get("source_glyph_hypotheses", [])})
    return {"row_label": ctx["row"], "column_headers": ctx["column"], "statement_titles": ctx["blocks"], "printed": glyphs[:2],
            "filed": {"concept": binding["field"].split("}")[-1], "period": period, "dimensions": dims, "unit": unit, "value": binding["quantity"]}}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--split", choices=("dev", "heldout"), required=True); ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--provider", choices=tuple(L.PROVIDERS), default="deepseek"); ap.add_argument("--model", default=None)
    args = ap.parse_args()
    model = args.model or MODEL
    if args.split == "dev":
        bank, perception, contexts_file, dest = BANK, PERCEPTION, BANK / "prior_v27/table_contexts.json", BANK / "llm_baseline_v30/fewshot_history" / (args.provider + "_" + model)
        tasks = json.loads((bank / "public/tasks.json").read_text())
        teachers = {k: v["teacher_binding"] for k, v in json.loads(TEACHERS.read_text()).items() if v.get("teacher_binding")}
        prefixes = json.loads((ALIGN / "learning_full_transfer/feedback_prefixes.json").read_text())["hash_prefix"]
        history = {c: prefixes[c]["10"] for c in prefixes}
        refs_of = lambda future: scoring_references(future)[0]
    else:
        bank, perception, contexts_file, dest = HELD / "bank", HELD, HELD / "bank/table_contexts.json", HELD / "llm/fewshot_history" / (args.provider + "_" + model)
        tasks = json.loads((bank / "public/tasks.json").read_text())
        teachers = {k: v["teacher_binding"] for k, v in json.loads((HELD / "training_only/past_binding_feedback.json").read_text()).items()}
        history = {}
        for t in tasks:
            if t["source_year"] == 2023:
                history.setdefault(t["company"], []).append(t["task_id"])
        history = {c: hash_order(v)[:10] for c, v in history.items()}
        allrefs = json.loads((HELD / "scoring_only/references.json").read_text())
        refs_of = lambda future: {k: allrefs[k] for k in future}
    pages = json.loads((perception / "localization/public_pages.json").read_text()); anchors = json.loads((perception / "localization/public_anchors.json").read_text())
    contexts = json.loads(contexts_file.read_text())
    by_id = {t["task_id"]: t for t in tasks}
    future = [t for t in tasks if t["source_year"] != 2023]
    if args.limit:
        future = future[:args.limit]
    (dest / "answers").mkdir(parents=True, exist_ok=True)
    key = L.load_key(args.provider)
    for name, _ in L.PROVIDERS.values():
        os.environ.pop(name, None)
    client = OpenAI(api_key=key, base_url=L.PROVIDERS[args.provider][1], timeout=180, max_retries=0)
    quota = httpx.Client(timeout=30)
    initial = balance(quota, key)[1] if args.provider == "deepseek" else 0.0
    save(dest / "design.json", {"model": model, "provider": args.provider, "split": args.split, "history_cells": 10, "history_order": "hash_prefix (same as the system's budget-10 feedback)",
                                "conventions_sha256": hashlib.sha256(CONVENTIONS.encode()).hexdigest(), "candidates_identical_to_learners": True, "started_utc": datetime.now(timezone.utc).isoformat()})
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "requests": 0}
    for n, task in enumerate(future, 1):
        out = dest / "answers" / (task["task_id"] + ".json")
        if out.exists():
            continue
        if n % 40 == 0 and args.provider == "deepseek":
            _, current = balance(quota, key); save(dest / "account_probe.json", {"spent_cny": initial - current})
            if initial - current > 150:
                print("spend cap reached", flush=True); break
        lists, fields, periods, dims, units = L.candidate_lists(task)
        member_labels = {m["field"]: (m.get("labels") or [m["name"]])[0] for m in task["dimension_member_candidates"]}
        examples = []
        for hid in history.get(task["company"], []):
            ht = by_id.get(hid); hb = teachers.get(hid)
            if ht is None or hb is None:
                continue
            hl = {m["field"]: (m.get("labels") or [m["name"]])[0] for m in ht["dimension_member_candidates"]}
            examples.append(describe(ht, contexts[hid], hb, hl))
        page = pages[anchors[task["task_id"]]["page_hash"]]; ctx = contexts[task["task_id"]]
        body = {"filing": filing_meta(task["report"]), "target": {"printed_glyphs_in_box": lists.pop("quantity_glyphs"), "row_label_ocr": ctx["row"], "column_headers_ocr": ctx["column"], "statement_titles_ocr": ctx["blocks"]},
                "same_company_examples_from_the_2023_filing": examples, "candidates": lists}
        img = L.crop_image(page, task["target_box"])
        content = [{"type": "text", "text": L.PROMPT + "\n" + CONVENTIONS + "\nThe image shows the table region with the target cell in a red box. Ten cells of the same company's previous filing are given with their filed tuples as examples of this company's conventions.\n\nTARGET, EXAMPLES AND CANDIDATES:\n" + json.dumps(body, ensure_ascii=False)},
                   {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(img).decode(), "detail": "high"}}]
        start = time.monotonic(); raw, error = None, None
        for attempt in range(2):
            try:
                extra = {"thinking": {"type": "disabled"}} if args.provider == "deepseek" else {}
                kwargs = {"response_format": {"type": "json_object"}} if args.provider == "deepseek" else {}
                reply = client.chat.completions.create(model=model, temperature=0, max_tokens=(400 if args.provider == "deepseek" else 2500), extra_body=extra, messages=[{"role": "user", "content": content}], **kwargs)
                raw = {"content": reply.choices[0].message.content, "finish_reason": reply.choices[0].finish_reason, "returned_model": reply.model, "usage": reply.usage.model_dump() if reply.usage else {}, "seconds": time.monotonic() - start}
                break
            except Exception as exc:  # noqa: BLE001
                error = repr(exc)[:300]; time.sleep(8 if "429" in error else 3)
        answer = None
        if raw:
            try:
                text = raw["content"].strip(); m = re.search(r"\{.*\}", text, re.S); answer = json.loads(m.group(0)) if m else None
            except (json.JSONDecodeError, AttributeError):
                answer = None
            usage["prompt_tokens"] += raw["usage"].get("prompt_tokens", 0); usage["completion_tokens"] += raw["usage"].get("completion_tokens", 0); usage["requests"] += 1
        binding = L.to_binding(answer, task, fields, periods, dims, units) if answer else None
        save(out, {"task_id": task["task_id"], "answer": answer, "predicted_binding": binding, "raw": raw, "error": error, "examples": len(examples)})
        if n % 20 == 0:
            print(args.split, n, "/", len(future), usage, flush=True)
    refs = refs_of({t["task_id"]: t for t in future})
    rows = {}
    for t in future:
        f = dest / "answers" / (t["task_id"] + ".json")
        if not f.exists():
            continue
        b = json.loads(f.read_text())["predicted_binding"]
        rows[t["task_id"]] = component_correct(b, refs[t["task_id"]]) if b else {k: False for k in ("field", "entity", "period", "unit", "dimensions", "quantity", "semantic_tuple_exact")}
    if rows:
        acc = {c: sum(r[c] for r in rows.values()) / len(rows) for c in next(iter(rows.values()))}
        companies = sorted({t["company"] for t in future}); rng = np.random.default_rng(0)
        vals = {c: [rows[t["task_id"]]["semantic_tuple_exact"] for t in future if t["company"] == c and t["task_id"] in rows] for c in companies}
        boots = [np.mean([v for i in rng.choice(len(companies), len(companies), replace=True) for v in vals[companies[i]]]) for _ in range(2000)]
        save(dest / "results.json", {"split": args.split, "model": model, "provider": args.provider, "scored": len(rows), "accuracy": acc, "exact_ci95_company_cluster": [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))],
                                     "per_company": {c: float(np.mean(v)) for c, v in vals.items() if v}, "usage": usage, "finished_utc": datetime.now(timezone.utc).isoformat()})
        print(args.split, "scored", len(rows), {k: round(v, 3) for k, v in acc.items()}, flush=True)


if __name__ == "__main__":
    main()
