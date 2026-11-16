# -*- coding: utf-8 -*-
"""算术见证(DR-135):同一行里,一列等于另外两列的和、差、积或商。

年报绑定的实验里,「总计 = 分项之和」这类算术关系是可靠的证据。业务库里同样常见:
毛利 = 金额 − 成本,金额 = 数量 × 单价。这些关系在数据上逐行成立时,可以:
- 核验构建时模型提出的派生公式(此前派生公式一律标为「尚未执行核验」);
- 在问数时告诉模型哪些列之间有确定的算术关系,不必自己重算或猜测。

只读、确定性、不调模型。一个等式只报一次:几种等价写法(毛利 = 金额 − 成本、金额 = 毛利 + 成本)里,
取等式中在表里排在最后的那一列作为结果列。只用标准库。
"""
from __future__ import annotations

import itertools
import re
import sqlite3

__all__ = ["discover", "supports", "brief"]

SAMPLE = 5000          # 每张表最多取这么多行核对
MIN_ROWS = 20          # 有效行少于这个数不下结论
MAX_COLUMNS = 24       # 数值列多于这个数时只看前面的列,避免组合数过大
MIN_MATCH = 0.999      # 至少这个比例的行成立
_OPS = {"+": lambda a, b: a + b, "-": lambda a, b: a - b, "*": lambda a, b: a * b,
        "/": lambda a, b: a / b if b else None}


def _close(x, y):
    return y is not None and abs(x - y) <= max(1e-6 * max(abs(x), abs(y)), 0.011)


def _numeric_columns(con, table):
    cols = [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]
    rows = con.execute(f'SELECT {", ".join(chr(34) + c.replace(chr(34), chr(34) * 2) + chr(34) for c in cols)} '
                       f'FROM "{table}" LIMIT {SAMPLE}').fetchall() if cols else []
    numeric = []
    for i, c in enumerate(cols):
        vals = [r[i] for r in rows]
        nums = [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if len(nums) >= MIN_ROWS and len(nums) >= 0.9 * len(vals) and len(set(nums)) > 1:
            numeric.append((i, c))
    return numeric[:MAX_COLUMNS], rows


def _informative(rows, ti, ai, bi, op):
    """能区分这个关系与其他关系的行数:结果和两个输入都不为 0,且两个输入不相等。"""
    return sum(1 for r in rows if r[ti] and r[ai] and r[bi] and r[ai] != r[bi])


def discover(con, table):
    """→ [{"table", "column", "formula", "op", "inputs": [a, b], "rows", "matched"}]"""
    if not re.fullmatch(r"[A-Za-z_][\w]*", table or ""):
        return []
    try:
        numeric, rows = _numeric_columns(con, table)
    except sqlite3.Error:
        return []
    found, seen = [], set()
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)      # noqa: E731
    for (ti, target), (ai, a), (bi, b) in itertools.permutations(numeric, 3):
        if ti < ai or ti < bi:                               # 等价的写法里,以排在最后的列为结果列
            continue
        for op, fn in _OPS.items():
            if op in "+*" and ai > bi:                       # 加法、乘法与次序无关,只试一次
                continue
            members = (frozenset((ti, ai, bi)), "add" if op in "+-" else "mul")
            if members in seen:
                continue
            probe = [r for r in rows[:40] if num(r[ti]) and num(r[ai]) and num(r[bi])]
            if not probe or not all(_close(r[ti], fn(r[ai], r[bi])) for r in probe[:20]):
                continue                                     # 前几行不成立的,不再全量核对
            usable = [r for r in rows if num(r[ti]) and num(r[ai]) and num(r[bi])]
            if len(usable) < MIN_ROWS:
                continue
            bad, limit = 0, len(usable) * (1 - MIN_MATCH)
            for r in usable:
                if not _close(r[ti], fn(r[ai], r[bi])):
                    bad += 1
                    if bad > limit:
                        break
            if bad > limit:
                continue
            if _informative(usable, ti, ai, bi, op) < max(MIN_ROWS, 0.2 * len(usable)):
                continue                                     # 多数行是 0 = 0 × x、0 = a − a 这类对什么关系都成立的行
            seen.add(members)
            found.append({"table": table, "column": target, "formula": f"{a} {op} {b}", "op": op, "inputs": [a, b],
                          "rows": len(usable), "matched": len(usable) - bad})
    return found


_COL = r"(?:[A-Za-z_]\w*\.)?([A-Za-z_]\w*)"
_BINARY = re.compile(r"\s*" + _COL + r"\s*([-+*/])\s*" + _COL + r"\s*")
_MATH = re.compile(r"[A-Za-z0-9_.\s()+\-*/]*[+\-*/][A-Za-z0-9_.\s()+\-*/]*")


def supports(formula, witnesses):
    """派生公式里的算式恰好是「列 运算 列」,且与某条见证一致时返回那条见证;否则返回 None。
    算式里还有别的项(如「(金额 − 成本)/ 金额」)时不算:见证只说明两列之间的关系。"""
    for seg in _MATH.findall(str(formula or "")):
        body = seg.strip()
        while body.startswith("(") and body.endswith(")"):
            body = body[1:-1].strip()
        m = _BINARY.fullmatch(body)
        if not m:
            continue
        a, op, b = m.group(1).lower(), m.group(2), m.group(3).lower()
        for w in witnesses or []:
            x, y = (s.lower() for s in w["inputs"])
            if w["op"] == op and ((a, b) == (x, y) or (op in "+*" and (a, b) == (y, x))):
                return w
    return None


def brief(witnesses, tables):
    """问数上下文里的说明行,只列所给表的。"""
    keep = {t.lower() for t in tables or []}
    return [f"{w['table']}.{w['column']} = {w['formula']}(数据核验:{w['matched']}/{w['rows']} 行)"
            for w in witnesses or [] if w["table"].lower() in keep]
