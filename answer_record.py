#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""答案的结果记录与数值匹配(DR-122、DR-128)。

一个答案里,表格、文字说明和依据卡各有各的数字。这里做两件事,让它们对得上:

1. **结果记录**:每个查询结果按「SQL + 返回的数据」得到一个编号,同样的查询在同样的数据上编号相同;
   答案带一份记录,列出用的是哪张图谱、哪个版本状态、哪几个结果。表格、依据卡、导出都引用这个编号。
2. **数值匹配**:文字说明里的数字逐个到查询结果里找对应的数值——结果里的值、行数、合计 / 最大 / 最小 / 平均、
   相邻值的差与增长率。匹配时看三样(DR-128):
   - 符号:写了负号的数字只和负值匹配;没写负号的数字要和负值匹配,前后得有「下降、减少、亏损」这类词。
   - 单位:数字后面写了「万」「亿」时,按这个单位和列的单位换算后比较;没写单位时只按原值比较。
   - 所属行:一句话里点了某一行的名字(如客户名),这句话里的数字就要出自那一行,或者是整张表的汇总值。
   每个匹配上的数字记下它对应结果里的哪一行哪一列,或者哪一种汇总。

这项检查只说明「文字里的数字在结果里有对应的数值」,不说明推理过程正确,所以叫数值匹配,不叫核验。
只做确定性处理,不调模型,不依赖 Web 层。宁可放过也不误报:只检查有小数、带百分号、带千分位或不小于 100 的数字,
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
_UNITS = {"万": 1e4, "亿": 1e8}
_DOWN = re.compile(r"下降|减少|降低|下滑|下跌|回落|缩减|亏损|负|跌|降|少|低")
_CLAUSE = re.compile(r"[,。;;\n!?!?]|,(?!\d)")
MAX_LABELS = 400             # 参与「所属行」判断的行名个数上限


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
    return [(n["token"], n["value"], n["decimals"], n["percent"]) for n in _numbers(text, ignore)]


def _numbers(text, ignore=""):
    """同 numbers_in,另带:紧跟的单位(万 / 亿)、是否写了负号、前后有没有表示减少的词、所在的那一句。"""
    cleaned = _DATE.sub(lambda m: " " * len(m.group(0)), str(text or ""))
    skip = {m.group(0).rstrip("%").replace(",", "") for m in _NUM.finditer(_DATE.sub(" ", str(ignore or "")))}
    bounds = [0] + [m.end() for m in _CLAUSE.finditer(cleaned)] + [len(cleaned)]
    out, seen = [], set()
    for m in _NUM.finditer(cleaned):
        tok = m.group(0)
        val = _num(tok)
        body = tok.rstrip("%").replace(",", "")
        if val is None or body in skip or tok in seen:
            continue
        if not ("." in body or tok.endswith("%") or "," in tok or abs(val) >= 100):
            continue
        seen.add(tok)
        after = cleaned[m.end():m.end() + 3].lstrip()
        unit = next((u for u in _UNITS if after.startswith(u)), "") if not tok.endswith("%") else ""
        start = max(x for x in bounds if x <= m.start())
        end = min(x for x in bounds if x >= m.end())
        out.append({"token": tok, "value": val, "decimals": _decimals(tok), "percent": tok.endswith("%"), "unit": unit,
                    "negative": tok.startswith("-"),
                    "down": bool(_DOWN.search(cleaned[max(start, m.start() - 10):m.start()] + cleaned[m.end():min(end, m.end() + 5)])),
                    "clause": str(text or "")[start:end]})
    return out


def _col_unit(name):
    """列名里写明的单位(收入_万、金额(亿元))→ 换算系数;没写返回 None。"""
    n = str(name or "")
    return next((f for u, f in _UNITS.items() if re.search(u + r"元?[))]?$|[_((]" + u, n)), None)


def _cell_num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str) and len(v) <= 24 and re.fullmatch(r"-?[\d,]+(\.\d+)?%?", v.strip()):
        return _num(v)
    return None


def _known(results, extra=()):
    """查询结果里能当作出处的数值 → ([(数值, 列的单位系数或 None, 出处说明)] 单元格, 同样结构的汇总与推算值)。"""
    cells: list[tuple[float, float | None, str]] = []
    derived: list[tuple[float, float | None, str]] = []
    for v in extra or ():
        n = _num(v) if isinstance(v, str) else (float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None)
        if n is not None:
            derived.append((n, None, "依据卡中的值"))
    for ri, r in enumerate(results or []):
        all_rows = (r.get("data") or {}).get("rows") or []
        name = str(r.get("title") or f"结果{ri + 1}")
        derived.append((float(len(all_rows)), None, f"{name}的行数"))
        cols: dict[str, list[float]] = {}
        for i, row in enumerate(all_rows[:MAX_ROWS]):
            if not isinstance(row, dict):
                continue
            for k, v in row.items():
                n = _cell_num(v) if k != "_row" else None
                if n is not None:
                    cells.append((n, _col_unit(k), f"{name} 第 {row.get('_row', i + 1)} 行「{k}」"))
                    cols.setdefault(str(k), []).append(n)
        for col, series in cols.items():
            if len(series) < 2:
                continue
            u = _col_unit(col)
            total = sum(series)
            derived += [(total, u, f"「{col}」合计"), (max(series), u, f"「{col}」最大值"), (min(series), u, f"「{col}」最小值"),
                        (total / len(series), u, f"「{col}」平均值"), (max(series) - min(series), u, f"「{col}」最大值与最小值之差")]
            head = series[:MAX_PAIR_ROWS]
            pairs = [(i, i + 1) for i in range(len(head) - 1)] + [(0, len(head) - 1)]
            for i, j in pairs:
                x, y = head[i], head[j]
                where = f"「{col}」第 {i + 1} 行与第 {j + 1} 行"
                derived += [(y - x, u, where + "之差"), (x - y, u, where + "之差")]
                if x:
                    derived += [((y - x) / x * 100, None, where + "的增长率"), (y / x * 100, None, where + "之比")]
                if y:
                    derived.append(((x - y) / y * 100, None, where + "的增长率"))
            if total:
                derived += [(x / total * 100, None, f"「{col}」第 {i + 1} 行占合计的比例") for i, x in enumerate(head)]
    return cells, derived


def known_values(results, extra=()):
    """查询结果里能当作出处的数值(不带出处说明)。"""
    cells, derived = _known(results, extra)
    return {v for v, _u, _s in cells + derived}


def _find(n, known):
    """这个数字对应已知数值里的哪一个 → (出处说明, "") | (None, "sign") | (None, "")。允许四舍五入。"""
    tol = 0.5 * 10 ** (-n["decimals"]) + 1e-9
    value, flipped = n["value"], False
    for k, unit, src in known:
        if n["percent"]:
            scales: tuple[float, ...] = (1.0, 100.0)                    # 结果里是百分数,或是 0~1 的比率
        elif n["unit"]:
            f = _UNITS[n["unit"]]
            scales = ((unit / f),) if unit else (1.0 / f, 1.0)             # 列写明了单位:按两个单位换算;没写:原值或已是该单位
        else:
            scales = (1.0,)
        for s in scales:
            if abs(k * s - value) <= tol:
                return src, ""
            if abs(-k * s - value) <= tol and value > 0:               # 结果是负值而文字没写负号:前后要有表示减少的词
                if n["down"]:
                    return src, ""
                flipped = True
    return None, ("sign" if flipped else "")


def _labels(results):
    """结果里可以当作行名的文字 → {文字: [(结果序号, 行)]}。"""
    out: dict[str, list[tuple[int, dict]]] = {}
    for ri, r in enumerate(results or []):
        for i, row in enumerate(((r.get("data") or {}).get("rows") or [])[:MAX_ROWS]):
            if not isinstance(row, dict):
                continue
            for v in row.values():
                if isinstance(v, str) and 2 <= len(v.strip()) <= 40 and _cell_num(v) is None:
                    if v.strip() not in out and len(out) >= MAX_LABELS:
                        continue
                    out.setdefault(v.strip(), []).append((ri, {**row, "_row": i + 1}))
    return out


def _named(clause, labels):
    """这一句里点到的行名。含字母数字的行名要求前后不是字母数字(C1 不算点到 C10)。"""
    return [w for w in labels if (re.search(r"(?<![A-Za-z0-9])" + re.escape(w) + r"(?![A-Za-z0-9])", clause)
                                  if re.search(r"[A-Za-z0-9]", w) else w in clause)]


def check_narrative(narrative, results, question="", extra=()):
    """文字说明里的数字逐个找对应的数值。返回
    {"checked": 检查了几个, "unverified": [没通过的原文片段], "issues": [{"token", "kind", "detail"}], "matched": [{"token", "source"}]}。
    kind:no_source 结果里没有这个数值;sign 与结果符号相反;other_row 这个数值在结果里属于另一行。"""
    nums = _numbers(narrative, ignore=question)
    if not nums:
        return {"checked": 0, "unverified": []}
    cells, derived = _known(results, extra)
    labels = _labels(results)
    issues, matched = [], []
    for n in nums:
        named = _named(n["clause"], labels) if labels else []
        src, why = None, ""
        if named:
            rows: dict[int, list[dict]] = {}
            for w in named:
                for ri, row in labels[w]:
                    if row not in rows.setdefault(ri, []):
                        rows[ri].append(row)
            own = [{"title": (results[ri].get("title") or f"结果{ri + 1}"), "data": {"rows": rs}} for ri, rs in rows.items()]
            own_cells, own_derived = _known(own)
            src, why = _find(n, own_cells + [d for d in own_derived if "行数" not in d[2]])
            if not src and why != "sign":
                # 整张表的汇总值、依据卡里的值;最大 / 最小值本身是别的行的值,句子里说了「最高、最低」才算
                extreme = re.search(r"最[高大低小多少]|峰值|第一|垫底", n["clause"]) is not None
                src, why = _find(n, [d for d in derived if extreme or not d[2].endswith(("最大值", "最小值"))])
            if not src and why != "sign":
                other, _w = _find(dict(n, down=True), cells)
                if other:
                    issues.append({"token": n["token"], "kind": "other_row",
                                   "detail": f"「{named[0]}」这一句里的 {n['token']} 在结果里是{other}"})
                    continue
        else:
            src, why = _find(n, cells + derived)
        if src:
            matched.append({"token": n["token"], "source": src})
        elif why == "sign":
            issues.append({"token": n["token"], "kind": "sign", "detail": f"{n['token']} 在结果里是负值,文字里没有写出"})
        else:
            issues.append({"token": n["token"], "kind": "no_source", "detail": f"{n['token']} 在结果里没有匹配的数值"})
    return {"checked": len(nums), "unverified": [i["token"] for i in issues][:8], "issues": issues[:8], "matched": matched[:20]}


def flag_text(check):
    """给使用者看的提示;没有问题返回空串。"""
    check = check or {}
    issues = check.get("issues") or [{"detail": f"{t} 在结果里没有匹配的数值"} for t in check.get("unverified") or []]
    if not issues:
        return ""
    return "数值匹配:文字说明里的 " + ";".join(i["detail"] for i in issues) + "。可能是推算或笔误,请以表格为准"
