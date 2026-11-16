# -*- coding: utf-8 -*-
"""反馈的回放验证(DR-137)。只用标准库。

此前「待改进」的反馈进了待处理列表以后,只能由人点「标记已处理」,看不出本体改过之后这个问题是否真的答对了;
「有用」的反馈只计数,没有别的用途。这里给出两样东西:
- snapshot  反馈时保存答案里每个结果的数据摘要(行集合的摘要、行数、最多 SAMPLE_ROWS 行样例),不保存全量数据。
- compare   按当前本体重新问一次后,比对新旧答案的数据:旧答案的每个结果,在新答案里能找到行集合相同、
            或在样例上按列容忍口径一致且行数相同的结果,就算一致。比的是数据,不是 SQL 文本。
- explain   结论的含义取决于反馈的类型:「待改进」的答案变了,说明修改起了作用(是否改对仍需人确认);
            「有用」的答案变了,提示可能退化。
"""
from __future__ import annotations

import hashlib
import json

import qa_eval_stats

__all__ = ["snapshot", "compare", "combine", "explain", "SAMPLE_ROWS", "MAX_RESULTS"]

SAMPLE_ROWS = 50
MAX_RESULTS = 8


def _rows(data):
    cols = list((data or {}).get("columns") or [])
    rows = []
    for r in (data or {}).get("rows") or []:
        rows.append([r.get(c) for c in cols] if isinstance(r, dict) else list(r))
    return cols, rows


def _digest(cols, rows):
    norm = sorted({json.dumps([qa_eval_stats._norm(v) for v in r], ensure_ascii=False, default=str) for r in rows})
    return hashlib.sha1("\n".join(norm).encode("utf-8")).hexdigest()[:16]


def snapshot(results):
    out = []
    for r in (results or [])[:MAX_RESULTS]:
        if not isinstance(r, dict):
            continue
        cols, rows = _rows(r.get("data"))
        out.append({"title": str(r.get("title") or "")[:120], "sql": str(r.get("sql") or "")[:4000], "columns": cols[:60],
                    "row_count": len(rows), "digest": _digest(cols, rows),
                    "rows": [[qa_eval_stats._norm(v) for v in row] for row in rows[:SAMPLE_ROWS]]})
    return out


def _same(a, b):
    if a["digest"] == b["digest"]:
        return True
    if a["row_count"] != b["row_count"] or a["row_count"] > SAMPLE_ROWS:
        return False
    return qa_eval_stats.compare((a["columns"], [tuple(r) for r in a["rows"]]),
                                 (b["columns"], [tuple(r) for r in b["rows"]]))["columns"]


def compare(before, after):
    """→ {"outcome": same | changed | no_answer | no_baseline, "matched": 旧结果中找到对应的个数, "of": 旧结果个数}"""
    if not before:
        return {"outcome": "no_baseline", "matched": 0, "of": 0}
    if not after:
        return {"outcome": "no_answer", "matched": 0, "of": len(before)}
    matched = sum(1 for b in before if any(_same(b, a) for a in after))
    return {"outcome": "same" if matched == len(before) else "changed", "matched": matched, "of": len(before)}


def combine(outcomes):
    """多次回放的结论(DR-138):各次一致时沿用;不一致时为 unstable,说明答案受模型每次写法不同的影响。"""
    kinds = set(outcomes or [])
    if not kinds:
        return "no_answer"
    return outcomes[0] if len(kinds) == 1 else "unstable"


_TEXT = {
    ("needs_work", "same"): "回放后答案的数据与反馈时一致,没有变化:反馈指出的问题可能仍然存在",
    ("needs_work", "changed"): "回放后答案的数据与反馈时不同:修改起了作用,请确认现在的答案是否正确",
    ("useful", "same"): "回放后答案的数据与反馈时一致",
    ("useful", "changed"): "回放后答案的数据与反馈时不同,可能退化,请复核",
}


def explain(verdict, outcome):
    if outcome == "unstable":
        return "多次回放的结果彼此不一致:答案受模型随机性影响,不能据此判断本体修改是否起了作用"
    if outcome == "no_answer":
        return "回放没有得到答案(查询失败或被拦下),请查看执行记录"
    if outcome == "no_baseline":
        return "反馈时没有保存答案的数据,无法比对;回放结果仅供查看"
    return _TEXT.get((verdict, outcome), outcome)
