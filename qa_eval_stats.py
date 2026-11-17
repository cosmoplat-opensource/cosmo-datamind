# -*- coding: utf-8 -*-
"""问数评测的比对规则与统计(DR-134)。只用标准库,不连数据库。

- compare        参考结果与模型结果的比对,同时给出两种口径:
                 strict  按 BIRD 的规则,两边的行作为集合相等(列序有意义,重复行不计);
                 columns 列容忍,模型结果可以多带列、列序可以不同,只要参考答案的每一列都能在模型结果里找到
                         一列与之对应,且按这种对应取出的行集合与参考相等。
                 数值统一保留 6 位小数后比较,整数与小数不区分。
- paired_ci      两组按题配对的差值及其 95% 区间(按题有放回重抽样,固定种子,结果可复现)。
- question_means 重复运行时,先在每道题内求平均,再做配对比较。
- in_context / reference_columns
                 候选上限:参考答案用到的表和列是否出现在给模型的上下文里。只列出表名、没有给列的不算。
- arm_summary    一组的正确率、候选上限,以及上限内的正确率(上下文里有所需的表和列时,模型选对的比例)。
"""
from __future__ import annotations

import itertools
import random
import re

__all__ = ["compare", "paired_ci", "question_means", "in_context", "reference_columns", "arm_summary"]

_SEED = 20261116
_BOOT = 2000


def _norm(v):
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        f = round(float(v), 6)
        return 0.0 if f == 0 else f
    if v is None:
        return None
    s = str(v).strip()
    try:
        return _norm(float(s)) if re.fullmatch(r"-?\d+(?:\.\d+)?", s) else s
    except ValueError:
        return s


def _rows(result):
    cols, rows = result
    out = []
    for r in rows or []:
        if isinstance(r, dict):
            out.append(tuple(_norm(r.get(c)) for c in cols))
        else:
            out.append(tuple(_norm(v) for v in r))
    return list(cols or []), out


def compare(gold, pred):
    """gold / pred 为 (列名列表, 行列表);行可以是元组或以列名为键的字典。→ {"strict": bool, "columns": bool}"""
    gcols, grows = _rows(gold)
    pcols, prows = _rows(pred)
    strict = len(gcols) == len(pcols) and set(grows) == set(prows)
    columns = strict
    if not columns and gcols and len(pcols) >= len(gcols) and len(pcols) <= 12:
        gset = set(grows)
        # 先按列的取值集合筛出可能对应的列,再在候选里找一一对应
        gvals = [{r[i] for r in grows} for i in range(len(gcols))]
        pvals = [{r[j] for r in prows} for j in range(len(pcols))]
        options = [[j for j in range(len(pcols)) if pvals[j] == gvals[i]] for i in range(len(gcols))]
        if all(options):
            for pick in itertools.product(*options):
                if len(set(pick)) == len(pick) and {tuple(r[j] for j in pick) for r in prows} == gset:
                    columns = True
                    break
    return {"strict": strict, "columns": columns}


def paired_ci(a, b, level=0.95):
    """a、b 为同一批题目上两组的得分(0/1 或按题的平均)。→ {"n", "diff", "lo", "hi"}"""
    n = min(len(a), len(b))
    if not n:
        return {"n": 0, "diff": 0.0, "lo": 0.0, "hi": 0.0}
    d = [float(a[i]) - float(b[i]) for i in range(n)]
    mean = sum(d) / n
    rng = random.Random(_SEED)
    boots = sorted(sum(d[rng.randrange(n)] for _ in range(n)) / n for _ in range(_BOOT))
    k = int((1 - level) / 2 * _BOOT)
    return {"n": n, "diff": mean, "lo": boots[k], "hi": boots[_BOOT - 1 - k]}


def question_means(runs_by_question):
    """{题号: [每次运行是否正确]} → {题号: 平均正确率}"""
    return {q: (sum(1 for x in runs if x) / len(runs) if runs else 0.0) for q, runs in runs_by_question.items()}


def _word(name, text):
    return re.search(r"(?<![\w.])" + re.escape(name) + r"(?![\w])", text, re.I) is not None


def in_context(context, tables, columns):
    """参考答案用到的表和列是否都在上下文里:表名作为整词出现;每一列在某一行里与它所在的表一起出现。"""
    text = context or ""
    if not all(_word(t, text) for t in tables or []):
        return False
    lines = [ln for ln in text.splitlines() if any(_word(t, ln) for t in tables or [])]
    body = "\n".join(lines)
    return all(_word(c, body) for c in columns or [])


def reference_columns(sql, table_columns):
    """参考 SQL 里出现的、属于所用表的列名(按整词、去重、保持出现顺序)。table_columns: {表: [列]}"""
    known = {c.lower(): c for cols in (table_columns or {}).values() for c in cols}
    stripped = re.sub(r"'(?:[^']|'')*'", " ", sql or "")
    out = []
    for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", stripped):
        c = known.get(tok.lower())
        if c and c not in out:
            out.append(c)
    return out


def arm_summary(rows, arm):
    """rows: 评测结果的逐题记录;每题 arms[arm] = {"runs": [{"correct": bool}], "ceiling": bool}。"""
    accs, ceil, inside = [], [], []
    for row in rows or []:
        rec = (row.get("arms") or {}).get(arm) or {}
        runs = rec.get("runs") or []
        if not runs:
            continue
        acc = sum(1 for r in runs if r.get("correct")) / len(runs)
        accs.append(acc)
        if rec.get("ceiling") is not None:
            ceil.append(bool(rec["ceiling"]))
            if rec["ceiling"]:
                inside.append(acc)
    mean = lambda xs: (sum(xs) / len(xs)) if xs else None      # noqa: E731
    return {"n": len(accs), "accuracy": mean(accs), "ceiling": mean([1.0 if c else 0.0 for c in ceil]),
            "accuracy_given_ceiling": mean(inside)}
