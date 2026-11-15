#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""答案的结果记录与同源数字核对(DR-122)。

一个答案里,表格、文字说明和依据卡各有各的数字。这里做两件事,让它们对得上:

1. **结果记录**:每个查询结果按「SQL + 返回的数据」得到一个编号,同样的查询在同样的数据上编号相同;
   答案带一份记录,列出用的是哪张图谱、哪个版本状态、哪几个结果。表格、依据卡、导出都引用这个编号。
2. **数字核对**:文字说明里的数字逐个到查询结果里找出处——结果里的值、行数、合计 / 最大 / 最小 / 平均、
   相邻值的差与增长率、常见的单位换算(万、亿、百分比)。找不到出处的数字列出来,由调用方提示「以表格为准」。

只做确定性处理,不调模型,不依赖 Web 层。核对宁可放过也不误报:只检查有小数、带百分号、带千分位或不小于 100 的数字,
问句里出现过的数字、日期、编号里的数字不检查。
"""
from __future__ import annotations

import hashlib
import json
import re
import time

MAX_ROWS = 2000              # 参与核对的行数上限
MAX_PAIR_ROWS = 60           # 计算差与增长率时每列最多取的行数
_DATE = re.compile(r"(?<!\d)\d{4}[-/年]\d{1,2}(?:[-/月]\d{1,2}日?)?|(?<!\d)\d{1,2}月\d{1,2}[日号]?|(?<!\d)\d{1,2}:\d{2}(?::\d{2})?")
_NUM = re.compile(r"(?<![A-Za-z0-9_.\-/])-?\d[\d,]*(?:\.\d+)?%?(?![A-Za-z0-9_/\-])")


def result_id(result):
    """查询结果的编号:SQL 与返回数据的摘要。数据或查询变了,编号就变。"""
    data = result.get("data") or {}
    blob = json.dumps([result.get("sql") or "", data.get("columns") or [], data.get("rows") or []],
                      ensure_ascii=False, sort_keys=True, default=str)
    return "r_" + hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def build(results, graphs=(), release="", source=""):
    """给每个结果写上编号,返回这份答案的结果记录。"""
    items = []
    for r in results or []:
        if not isinstance(r, dict):
            continue
        r["result_id"] = result_id(r)
        data = r.get("data") or {}
        items.append({"result_id": r["result_id"], "title": r.get("title") or "", "row_count": len(data.get("rows") or []),
                      "columns": list(data.get("columns") or [])[:60]})
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    answer = hashlib.sha1(("|".join(i["result_id"] for i in items) + "|" + ",".join(graphs or ())).encode()).hexdigest()[:12]
    return {"answer_id": "a_" + answer, "generated_at": stamp, "graphs": list(graphs or ()), "release": release or "",
            "source": source or "", "results": items}


def _num(text):
    try:
        return float(str(text).replace(",", "").rstrip("%"))
    except ValueError:
        return None


def _decimals(token):
    body = token.rstrip("%").replace(",", "")
    return len(body.split(".")[1]) if "." in body else 0


def numbers_in(text, ignore=""):
    """文字里值得核对的数字。返回 [(原文片段, 数值, 小数位数, 是否百分数)]。"""
    cleaned = _DATE.sub(" ", str(text or ""))
    skip = {m.group(0).rstrip("%").replace(",", "") for m in _NUM.finditer(_DATE.sub(" ", str(ignore or "")))}
    out, seen = [], set()
    for m in _NUM.finditer(cleaned):
        tok = m.group(0)
        val = _num(tok)
        body = tok.rstrip("%").replace(",", "")
        if val is None or body in skip or tok in seen:
            continue
        worth = "." in body or tok.endswith("%") or "," in tok or abs(val) >= 100
        if not worth:
            continue
        seen.add(tok)
        out.append((tok, val, _decimals(tok), tok.endswith("%")))
    return out


def known_values(results, extra=()):
    """查询结果里能当作出处的数值:单元格的值、行数、每列的合计 / 最大 / 最小 / 平均、相邻值与首尾值的差和比率。"""
    vals: set[float] = set()
    for v in extra or ():
        n = _num(v) if isinstance(v, str) else (float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None)
        if n is not None:
            vals.add(n)
    for r in results or []:
        rows = ((r.get("data") or {}).get("rows") or [])[:MAX_ROWS]
        vals.add(float(len((r.get("data") or {}).get("rows") or [])))
        cols: dict[str, list[float]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            for k, v in row.items():
                if isinstance(v, bool):
                    continue
                if isinstance(v, (int, float)):
                    n = float(v)
                elif isinstance(v, str) and len(v) <= 24:
                    n = _num(v) if re.fullmatch(r"-?[\d,]+(\.\d+)?%?", v.strip()) else None
                else:
                    n = None
                if n is not None:
                    vals.add(n)
                    cols.setdefault(str(k), []).append(n)
        for series in cols.values():
            if len(series) >= 2:
                vals.update((sum(series), max(series), min(series), sum(series) / len(series), max(series) - min(series)))
                head = series[:MAX_PAIR_ROWS]
                pairs = list(zip(head, head[1:], strict=False)) + [(head[0], head[-1])]
                for a, b in pairs:
                    vals.add(b - a)
                    vals.add(a - b)
                    if a:
                        vals.add((b - a) / a * 100)
                        vals.add(b / a * 100)
                    if b:
                        vals.add((a - b) / b * 100)
                total = sum(series)
                if total:
                    vals.update(x / total * 100 for x in head)
    return vals


def _matches(value, decimals, percent, known):
    """这个数字能否在已知数值里找到出处(允许四舍五入,允许万 / 亿 / 百分比换算)。"""
    tol = 0.5 * 10 ** (-decimals) + 1e-9
    scales = (1.0, 100.0) if percent else (1.0, 1e-4, 1e-8)        # 百分数:比率 × 100;其余:原值、万、亿
    for k in known:
        for s in scales:
            if abs(k * s - value) <= tol or abs(abs(k) * s - abs(value)) <= tol:
                return True
    return False


def check_narrative(narrative, results, question="", extra=()):
    """文字说明里的数字逐个找出处。返回 {"checked": 检查了几个, "unverified": [找不到出处的原文片段]}。"""
    nums = numbers_in(narrative, ignore=question)
    if not nums:
        return {"checked": 0, "unverified": []}
    known = known_values(results, extra)
    bad = [tok for tok, val, dec, pct in nums if not _matches(val, dec, pct, known)]
    return {"checked": len(nums), "unverified": bad[:8]}


def flag_text(check):
    """给使用者看的提示;没有问题返回空串。"""
    bad = (check or {}).get("unverified") or []
    if not bad:
        return ""
    return "文字说明里的数字 " + "、".join(bad) + " 在查询结果中找不到对应的值,可能是推算或笔误,请以表格为准"
