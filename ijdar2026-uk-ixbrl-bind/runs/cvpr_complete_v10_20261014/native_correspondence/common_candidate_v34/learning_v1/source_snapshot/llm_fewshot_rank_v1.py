"""Few-shot LLM readings for every target (teachers included), in the ranked format the typed priors consume.

Same inputs as llm_fewshot_baseline_v1 (crop with the cell marked, OCR context, identical public lists, the
company's ten budget-10 history cells with filed tuples, the conventions paragraph, filing metadata), but the
model is asked for the three most likely concepts plus period, dimensions, unit, quantity and sign, so that
  * rank-1 of everything gives a few-shot baseline prediction (scored on the 2024/25 targets only), and
  * field_top3 / period_index / dimensions_index feed the v33 priors exactly as the zero-shot field_rank pass
    fed v31/v32.
Teacher (2023) cells are read with their own cell removed from the history (leave-one-out), so no target ever
sees its own filed tuple.  Development: 840 targets; held-out: 200 targets.
"""

import argparse
import base64
import json
import os
import re
import time
from datetime import datetime, timezone

import httpx
import numpy as np
from openai import OpenAI
import llm_baseline_v30 as L
from llm_fewshot_baseline_v1 import CONVENTIONS, HELD, TEACHERS, describe, filing_meta, hash_order
from native_baseline import MODEL, balance
from native_header_alignment_v21 import DEST as ALIGN
from native_v30_bank import DEST as BANK
from native_v30_bank import PERCEPTION
from prepare import save
from score_native_binding_v17 import component_correct, scoring_references

RANK_PROMPT = """You are binding one numeric cell of a rendered UK annual report to its iXBRL fact.
The target cell is marked in red. From the field candidate list, rank the THREE most likely IFRS/extension concepts
for the row (consider the row label, the statement the table belongs to, the concept's balance and period type).
Also choose, by index, the period, the dimensions (index 0 may be "none") and the unit (the scale multiplier must
match the table's unit header), and give the printed quantity string and the iXBRL sign (+1 or -1).
Answer with JSON only:
{"field_top3": [i, j, k], "period": i, "dimensions": i, "unit": i, "quantity": "printed", "sign": 1}
"""
SPEND_CAP_CNY = 150.0


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--split", choices=("dev", "heldout"), required=True); ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    model = MODEL
    if args.split == "dev":
        bank, perception, contexts_file, dest = BANK, PERCEPTION, BANK / "prior_v27/table_contexts.json", BANK / "llm_fewshot_rank_v1"
        tasks = json.loads((bank / "public/tasks.json").read_text())
        teachers = {k: v["teacher_binding"] for k, v in json.loads(TEACHERS.read_text()).items() if v.get("teacher_binding")}
        prefixes = json.loads((ALIGN / "learning_full_transfer/feedback_prefixes.json").read_text())["hash_prefix"]
        history = {c: prefixes[c]["10"] for c in prefixes}
        refs_of = lambda future: scoring_references(future)[0]
    else:
        bank, perception, contexts_file, dest = HELD / "bank", HELD, HELD / "bank/table_contexts.json", HELD / "llm/fewshot_rank_v1"
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
    todo = list(tasks)[:args.limit] if args.limit else list(tasks)
    (dest / "answers").mkdir(parents=True, exist_ok=True)
    key = L.load_key("deepseek")
    for name, _ in L.PROVIDERS.values():
        os.environ.pop(name, None)
    client = OpenAI(api_key=key, base_url=L.PROVIDERS["deepseek"][1], timeout=180, max_retries=0)
    quota = httpx.Client(timeout=30)
    initial = balance(quota, key)[1]
    save(dest / "design.json", {"model": model, "split": args.split, "targets": len(tasks), "history_cells": "budget-10 hash-prefix cells of the same company, the target itself excluded",
                                "prompt_sha256": __import__("hashlib").sha256((RANK_PROMPT + CONVENTIONS).encode()).hexdigest(), "candidates_identical_to_learners": True,
                                "started_utc": datetime.now(timezone.utc).isoformat()})
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "requests": 0}
    for n, task in enumerate(todo, 1):
        out = dest / "answers" / (task["task_id"] + ".json")
        if out.exists():
            continue
        if n % 40 == 0:
            _, current = balance(quota, key); save(dest / "account_probe.json", {"spent_cny": initial - current})
            if initial - current > SPEND_CAP_CNY:
                print("spend cap reached", flush=True); break
        lists, fields, periods, dims, units = L.candidate_lists(task)
        examples = []
        for hid in history.get(task["company"], []):
            ht = by_id.get(hid); hb = teachers.get(hid)
            if hid == task["task_id"] or ht is None or hb is None:
                continue
            hl = {m["field"]: (m.get("labels") or [m["name"]])[0] for m in ht["dimension_member_candidates"]}
            examples.append(describe(ht, contexts[hid], hb, hl))
        page = pages[anchors[task["task_id"]]["page_hash"]]; ctx = contexts[task["task_id"]]
        body = {"filing": filing_meta(task["report"]), "target": {"printed_glyphs_in_box": lists.pop("quantity_glyphs"), "row_label_ocr": ctx["row"], "column_headers_ocr": ctx["column"], "statement_titles_ocr": ctx["blocks"]},
                "same_company_examples_from_the_2023_filing": examples, "candidates": lists}
        img = L.crop_image(page, task["target_box"])
        content = [{"type": "text", "text": RANK_PROMPT + "\n" + CONVENTIONS + "\nThe image shows the table region with the target cell in a red box. Cells of the same company's previous filing are given with their filed tuples as examples of this company's conventions.\n\nTARGET, EXAMPLES AND CANDIDATES:\n" + json.dumps(body, ensure_ascii=False)},
                   {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(img).decode(), "detail": "high"}}]
        start = time.monotonic(); raw, error = None, None
        for attempt in range(2):
            try:
                reply = client.chat.completions.create(model=model, temperature=0, max_tokens=400, extra_body={"thinking": {"type": "disabled"}}, response_format={"type": "json_object"},
                                                       messages=[{"role": "user", "content": content}])
                raw = {"content": reply.choices[0].message.content, "finish_reason": reply.choices[0].finish_reason, "returned_model": reply.model, "usage": reply.usage.model_dump() if reply.usage else {}, "seconds": time.monotonic() - start}
                break
            except Exception as exc:  # noqa: BLE001
                error = repr(exc)[:300]; time.sleep(8 if "429" in error else 3)
        answer = None
        if raw:
            try:
                m = re.search(r"\{.*\}", raw["content"].strip(), re.S); answer = json.loads(m.group(0)) if m else None
            except (json.JSONDecodeError, AttributeError):
                answer = None
            usage["prompt_tokens"] += raw["usage"].get("prompt_tokens", 0); usage["completion_tokens"] += raw["usage"].get("completion_tokens", 0); usage["requests"] += 1
        ranked, top_idx = [], []
        for i in (answer or {}).get("field_top3", []) or []:
            try:
                ranked.append("{" + fields[int(i)]["namespace"] + "}" + fields[int(i)]["name"]); top_idx.append(int(i))
            except (IndexError, ValueError, TypeError):
                continue
        binding = None
        if answer and top_idx:
            single = dict(answer); single["field"] = top_idx[0]
            binding = L.to_binding(single, task, fields, periods, dims, units)
        save(out, {"task_id": task["task_id"], "answer": answer, "field_top3": ranked, "period_index": (answer or {}).get("period"), "dimensions_index": (answer or {}).get("dimensions"),
                   "predicted_binding": binding, "raw": raw, "error": error, "examples": len(examples), "source_year": task["source_year"]})
        if n % 20 == 0:
            print(args.split, n, "/", len(todo), usage, flush=True)
    answered = len(list((dest / "answers").glob("*.json")))
    future = [t for t in tasks if t["source_year"] != 2023]
    refs = refs_of({t["task_id"]: t for t in future})
    rows = {}
    for t in future:
        f = dest / "answers" / (t["task_id"] + ".json")
        if not f.exists():
            continue
        b = json.loads(f.read_text())["predicted_binding"]
        rows[t["task_id"]] = component_correct(b, refs[t["task_id"]]) if b else {k: False for k in ("field", "entity", "period", "unit", "dimensions", "quantity", "semantic_tuple_exact")}
    result = {"split": args.split, "model": model, "answered": answered, "targets": len(tasks), "scored_future": len(rows), "usage": usage, "finished_utc": datetime.now(timezone.utc).isoformat()}
    if rows:
        acc = {c: sum(r[c] for r in rows.values()) / len(rows) for c in next(iter(rows.values()))}
        companies = sorted({t["company"] for t in future}); rng = np.random.default_rng(0)
        vals = {c: [rows[t["task_id"]]["semantic_tuple_exact"] for t in future if t["company"] == c and t["task_id"] in rows] for c in companies}
        boots = [np.mean([v for i in rng.choice(len(companies), len(companies), replace=True) for v in vals[companies[i]]]) for _ in range(2000)]
        result.update({"accuracy": acc, "exact_ci95_company_cluster": [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))], "per_company": {c: float(np.mean(v)) for c, v in vals.items() if v}})
        print(args.split, "rank-1 scored", len(rows), {k: round(v, 3) for k, v in acc.items()}, flush=True)
    save(dest / "results.json", result)


if __name__ == "__main__":
    main()
