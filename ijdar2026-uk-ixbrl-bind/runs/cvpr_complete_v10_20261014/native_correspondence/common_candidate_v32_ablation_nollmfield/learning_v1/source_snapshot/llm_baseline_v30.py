"""A3: zero-shot LLM baselines on the v30 development targets (text+layout and vision modes).

Both modes receive exactly the public candidate lists every learner sees (v30 bank) and
must answer by candidate index; nothing from reference or teacher files is sent.
  text   : OCR lines of the page (reading order), the target's row label, column headers
           and the printed glyph inside the box, plus the candidate lists.
  vision : the rendered page crop with the target box drawn in red, plus the same lists.
One request per target, temperature 0, JSON output, resumable per target, balance-guarded.
The DeepSeek credential is read from cosmo-datamind/.env at run time and never written out.
"""

import argparse
import base64
import io
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from native_baseline import MODEL, balance
from native_structural_prior_v27 import DEST as V27
from native_v26_bank import text_lines
from native_v30_bank import DEST as BANK
from native_v30_bank import PERCEPTION
from openai import OpenAI
from PIL import Image, ImageDraw
from prepare import save, sha
from score_native_binding_v17 import component_correct, scoring_references

DEST = BANK / "llm_baseline_v30"
ENV = Path(__file__).resolve().parents[2] / "cosmo-datamind/.env"
ENTITY_SCHEME = "http://standards.iso.org/iso/17442"
TOP_FIELDS, TOP_DIMS = 40, 25
SPEND_CAP_CNY = 80.0

RANK_PROMPT = """You are binding one numeric cell of a rendered UK annual report to its iXBRL fact.
The target cell is marked in red. From the field candidate list, rank the THREE most likely IFRS/extension concepts
for the row (consider the row label, the statement the table belongs to, the concept's balance and period type).
Also choose the period index and the dimensions index. Answer with JSON only:
{"field_top3": [i, j, k], "period": i, "dimensions": i}
"""

PROMPT = """You are binding one numeric cell of a rendered UK annual report to its iXBRL fact.
The target cell is marked. Choose, by index, exactly one candidate from each list below:
- field: the IFRS/extension concept of the row (consider the row label, statement, balance and period type),
- period: the reporting period of the column or row block the cell belongs to,
- dimensions: the dimensional qualifier (equity component column, segment, adjusting-items column; index 0 may be "none"),
- unit: currency/scale of the cell (the scale multiplier must match the table's unit header),
- quantity: the printed value string of the cell, and sign: +1 or -1 for the iXBRL value
  (parentheses usually mean a display negative, but concepts with a credit/debit balance may require the opposite sign).
Answer with JSON only: {"field": i, "period": i, "dimensions": i, "unit": i, "quantity": "printed", "sign": 1}
"""


PROVIDERS = {"deepseek": ("DEEPSEEK_API_KEY", "https://api.deepseek.com"), "poe": ("POE_API_KEY", "https://api.poe.com/v1")}


def load_key(provider):
    name = PROVIDERS[provider][0]
    for line in ENV.read_text().splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip().strip('"')
    raise RuntimeError("Credential missing for " + provider)


def candidate_lists(task):
    fields = sorted(task["field_candidates"], key=lambda c: -(0.65 * c["retrieval_scores"][0] + 0.25 * c["retrieval_scores"][1] + 0.1 * c["retrieval_scores"][2]))[:TOP_FIELDS]
    periods = [p for p in task["period_candidates"] if p["period"].get("complete")]
    seen, uniq = set(), []
    for p in periods:
        k = json.dumps({a: b for a, b in p["period"].items() if a != "complete"}, sort_keys=True)
        if k not in seen:
            seen.add(k); uniq.append(p)
    units = [u for u in task["unit_candidates"] if u.get("unit") and u.get("multiplier") is not None]
    useen, uuniq = set(), []
    for u in units:
        k = (json.dumps(u["unit"], sort_keys=True), str(u["multiplier"]))
        if k not in useen:
            useen.add(k); uuniq.append(u)
    dims = sorted(task["dimension_tuple_candidates"], key=lambda d: (-(d["dimensions"] == []), -d.get("retrieval_score", 0)))[:TOP_DIMS]
    labels = {m["field"]: (m.get("labels") or [m["name"]])[0] for m in task["dimension_member_candidates"]}
    return {
        "field": [{"i": i, "name": c["name"], "label": (c.get("labels") or [c["name"]])[0], "balance": c.get("balance"), "period_type": c.get("period_type"),
                   "type": (c.get("type") or "").split("}")[-1].replace("ItemType", ""), "extension": "ifrs" not in c["namespace"]} for i, c in enumerate(fields)],
        "period": [{"i": i, **{a: b for a, b in p["period"].items() if a != "complete"}} for i, p in enumerate(uniq)],
        "dimensions": [{"i": i, "members": [{"axis": d["axis"].split("}")[-1], "member": labels.get(d["member"], d["member"].split("}")[-1])} for d in t["dimensions"]] or "none"} for i, t in enumerate(dims)],
        "unit": [{"i": i, "unit": "/".join(x.split("}")[-1] for x in u["unit"]["numerator"]) + ("/" + "/".join(x.split("}")[-1] for x in u["unit"]["denominator"]) if u["unit"]["denominator"] else ""),
                  "multiplier": u["multiplier"]} for i, u in enumerate(uuniq)],
        "quantity_glyphs": sorted({g for q in task["quantity_candidates"] for g in q.get("source_glyph_hypotheses", [])}),
    }, fields, uniq, dims, uuniq


def page_text(words, tb, max_chars=9000):
    rows = text_lines(words)
    lines = []
    for ids in rows:
        yc = sum((words[i]["box"][1] + words[i]["box"][3]) / 2 for i in ids) / len(ids)
        mark = " <== TARGET ROW" if abs(yc - (tb[1] + tb[3]) / 2) <= 0.007 else ""
        lines.append(" ".join(words[i]["text"] for i in ids) + mark)
    text = "\n".join(lines)
    return text[:max_chars]


def crop_image(page, tb):
    with Image.open(page["image"]) as im:
        W, H = im.size
        x0, y0, x1, y1 = tb
        top, bottom = max(0, int((y0 - 0.25) * H)), min(H, int((y1 + 0.12) * H))
        crop = im.convert("RGB").crop((0, top, W, bottom))
        draw = ImageDraw.Draw(crop)
        draw.rectangle([int(x0 * W) - 6, int(y0 * H) - top - 6, int(x1 * W) + 6, int(y1 * H) - top + 6], outline=(255, 0, 0), width=5)
        scale = min(1.0, 1600 / crop.width)
        if scale < 1:
            crop = crop.resize((int(crop.width * scale), int(crop.height * scale)))
        buf = io.BytesIO(); crop.save(buf, format="PNG")
    return buf.getvalue()


def to_binding(answer, task, fields, periods, dims, units):
    try:
        f = fields[int(answer["field"])]; p = periods[int(answer["period"])]; d = dims[int(answer["dimensions"])]; u = units[int(answer["unit"])]
    except (KeyError, IndexError, ValueError, TypeError):
        return None
    printed = re.sub(r"[^\d.]", "", str(answer.get("quantity", "")).replace(",", ""))
    try:
        from decimal import Decimal
        from native_binding_features_v17 import quantity_value
        sign = -1 if int(answer.get("sign", 1)) < 0 else 1
        quantity = quantity_value(printed, u["multiplier"], sign) if printed else None
    except Exception:
        quantity = None
    return {"field": "{" + f["namespace"] + "}" + f["name"], "entity": {"scheme": ENTITY_SCHEME, "identifier": task["company"]},
            "period": {a: b for a, b in p["period"].items() if a != "complete"}, "dimensions": d["dimensions"], "unit": u["unit"], "quantity": quantity}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--mode", choices=("text", "vision", "field_rank"), required=True); ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--provider", choices=tuple(PROVIDERS), default="deepseek"); ap.add_argument("--model", default=None)
    args = ap.parse_args()
    model = args.model or MODEL
    dest = DEST / args.mode if args.provider == "deepseek" and model == MODEL else DEST / (args.provider + "_" + model.replace("/", "_")) / args.mode
    (dest / "answers").mkdir(parents=True, exist_ok=True)
    tasks = json.loads((BANK / "public/tasks.json").read_text())
    pages = json.loads((PERCEPTION / "localization/public_pages.json").read_text()); anchors = json.loads((PERCEPTION / "localization/public_anchors.json").read_text())
    contexts = json.loads((V27 / "table_contexts.json").read_text())
    future = [t for t in tasks if t["source_year"] != 2023] if args.mode != "field_rank" else list(tasks)
    if args.limit:
        future = future[:args.limit]
    key = load_key(args.provider)
    for name, _ in PROVIDERS.values():
        os.environ.pop(name, None)
    client = OpenAI(api_key=key, base_url=PROVIDERS[args.provider][1], timeout=180, max_retries=0)
    quota = httpx.Client(timeout=30)
    if args.provider == "deepseek":
        available, initial = balance(quota, key)
        if not available or initial < 10:
            raise RuntimeError("Insufficient available DeepSeek balance")
    else:
        available, initial = True, 0.0
    save(dest / "design.json", {"model": model, "provider": args.provider, "mode": args.mode, "temperature": 0, "thinking": "disabled", "top_fields": TOP_FIELDS, "top_dims": TOP_DIMS,
                                "prompt_sha256": sha(Path(__file__)), "candidates_identical_to_learners": True, "reference_or_teacher_files_sent": False, "started_utc": datetime.now(timezone.utc).isoformat()})
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "requests": 0}
    for n, task in enumerate(future, 1):
        out = dest / "answers" / (task["task_id"] + ".json")
        if out.exists():
            continue
        if n % 40 == 0 and args.provider == "deepseek":
            available, current = balance(quota, key)
            save(dest / "account_probe.json", {"current_balance_cny": current, "spent_cny": initial - current})
            if not available or initial - current > SPEND_CAP_CNY:
                print("spend cap reached", flush=True); break
        lists, fields, periods, dims, units = candidate_lists(task)
        page = pages[anchors[task["task_id"]]["page_hash"]]
        ctx = contexts[task["task_id"]]
        body = {"target": {"printed_glyphs_in_box": lists.pop("quantity_glyphs"), "row_label_ocr": ctx["row"], "column_headers_ocr": ctx["column"], "statement_titles_ocr": ctx["blocks"],
                           "company_lei": task["company"], "report_year": task["source_year"]}, "candidates": lists}
        if args.mode == "text":
            content = [{"type": "text", "text": PROMPT + "\nPAGE TEXT (OCR, reading order):\n" + page_text(page["words"], task["target_box"]) + "\n\nTARGET AND CANDIDATES:\n" + json.dumps(body, ensure_ascii=False)}]
        elif args.mode == "field_rank":
            img = crop_image(page, task["target_box"])
            body["candidates"] = {"field": body["candidates"]["field"], "period": body["candidates"]["period"], "dimensions": body["candidates"]["dimensions"]}
            content = [{"type": "text", "text": RANK_PROMPT + "\nThe image shows the table region with the target cell in a red box.\n\nTARGET AND CANDIDATES:\n" + json.dumps(body, ensure_ascii=False)},
                       {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(img).decode(), "detail": "high"}}]
        else:
            img = crop_image(page, task["target_box"])
            content = [{"type": "text", "text": PROMPT + "\nThe image shows the table region with the target cell in a red box.\n\nTARGET AND CANDIDATES:\n" + json.dumps(body, ensure_ascii=False)},
                       {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(img).decode(), "detail": "high"}}]
        start = time.monotonic(); raw, error = None, None
        for attempt in range(2):
            try:
                extra = {"thinking": {"type": "disabled"}} if args.provider == "deepseek" else {}
                kwargs = {"response_format": {"type": "json_object"}} if args.provider == "deepseek" else {}
                reply = client.chat.completions.create(model=model, temperature=0, max_tokens=(400 if args.provider == "deepseek" else 2500), extra_body=extra, messages=[{"role": "user", "content": content}], **kwargs)
                raw = {"content": reply.choices[0].message.content, "finish_reason": reply.choices[0].finish_reason, "returned_model": reply.model,
                       "usage": reply.usage.model_dump() if reply.usage else {}, "seconds": time.monotonic() - start}
                break
            except Exception as exc:  # noqa: BLE001
                error = repr(exc)[:300]; time.sleep(8 if "429" in error else 3)
        answer = None
        if raw:
            try:
                text = raw["content"].strip()
                if text.startswith("```"):
                    text = text.strip("`").split("\n", 1)[1] if "\n" in text else text.strip("`")
                    text = text[len("json"):] if text.lower().startswith("json") else text
                m = re.search(r"\{.*\}", text, re.S)
                answer = json.loads(m.group(0)) if m else None
            except (json.JSONDecodeError, AttributeError):
                answer = None
            usage["prompt_tokens"] += raw["usage"].get("prompt_tokens", 0); usage["completion_tokens"] += raw["usage"].get("completion_tokens", 0); usage["requests"] += 1
        if args.mode == "field_rank":
            ranked = []
            for i in (answer or {}).get("field_top3", []) or []:
                try:
                    ranked.append("{" + fields[int(i)]["namespace"] + "}" + fields[int(i)]["name"])
                except (IndexError, ValueError, TypeError):
                    continue
            save(out, {"task_id": task["task_id"], "answer": answer, "field_top3": ranked, "period_index": (answer or {}).get("period"), "dimensions_index": (answer or {}).get("dimensions"), "raw": raw, "error": error})
            if n % 20 == 0:
                print(args.mode, n, "/", len(future), usage, flush=True)
            continue
        binding = to_binding(answer, task, fields, periods, dims, units) if answer else None
        save(out, {"task_id": task["task_id"], "answer": answer, "predicted_binding": binding, "raw": raw, "error": error})
        if n % 20 == 0:
            print(args.mode, n, "/", len(future), usage, flush=True)
    if args.mode == "field_rank":
        save(dest / "results.json", {"mode": "field_rank", "model": model, "provider": args.provider, "answered": len(list((dest / "answers").glob("*.json"))), "usage": usage, "finished_utc": datetime.now(timezone.utc).isoformat()})
        print("field_rank done", usage, flush=True)
        return
    # score
    refs, _ = scoring_references({t["task_id"]: t for t in future})
    rows = {}
    for t in future:
        f = dest / "answers" / (t["task_id"] + ".json")
        if not f.exists():
            continue
        b = json.loads(f.read_text())["predicted_binding"]
        rows[t["task_id"]] = component_correct(b, refs[t["task_id"]]) if b else {k: False for k in ("field", "entity", "period", "unit", "dimensions", "quantity", "semantic_tuple_exact")}
    if rows:
        acc = {c: sum(r[c] for r in rows.values()) / len(rows) for c in next(iter(rows.values()))}
        companies = sorted({t["company"] for t in future}); import numpy as np
        rng = np.random.default_rng(0); vals = {c: [rows[t["task_id"]]["semantic_tuple_exact"] for t in future if t["company"] == c and t["task_id"] in rows] for c in companies}
        boots = [np.mean([v for i in rng.choice(len(companies), len(companies), replace=True) for v in vals[companies[i]]]) for _ in range(2000)]
        save(dest / "results.json", {"mode": args.mode, "model": model, "provider": args.provider, "scored": len(rows), "accuracy": acc, "exact_ci95_company_cluster": [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))],
                                     "usage": usage, "finished_utc": datetime.now(timezone.utc).isoformat()})
        print(args.mode, "scored", len(rows), {k: round(v, 3) for k, v in acc.items()}, flush=True)


if __name__ == "__main__":
    main()
