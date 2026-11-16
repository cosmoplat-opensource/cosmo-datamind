# -*- coding: utf-8 -*-
"""多级表头的识别与展开(DR-135)。只用标准库。

Excel 里用合并单元格做的两三行表头,导出成 CSV 后上层只在合并区域的第一格有值,其余为空:

    地区,2025年,,2026年,
    ,收入,成本,收入,成本
    华东,10,6,12,7

年报绑定的实验说明,这类表的难点在列归属:一个数值属于哪一年、哪个项目,写在上层表头里。这里做三件事:
- 识别表头有几行:开头连续几行里没有数值(四位年份不算数值);第一行要有空格或重复值(合并单元格的痕迹),
  第二行起的表头在左侧的键列位置为空。最多 3 行。
- 展开:上层表头在本行内向右补齐空格,各层用「_」连成一个列名(2025年_收入)。
- 上层是期间(年、季度、月)时另给一份长表:键列 + 期间 + 项目 + 值,便于按期间查数和作图。
"""
from __future__ import annotations

import re

__all__ = ["parse"]

_NUM = re.compile(r"^[-+]?(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?%?$")
_YEAR = re.compile(r"^(19|20)\d{2}$")
_PERIOD = re.compile(r"((19|20)\d{2}\s*年?|[一二三四1-4]\s*季度|Q[1-4]|[上下]半年|\d{1,2}\s*月|(19|20)\d{2}[-/.]\d{1,2}|"
                     r"FY\s*\d{2,4}|上年|本年|去年|今年|同期|期初|期末)", re.I)


def _is_num(v):
    v = (v or "").strip().replace(",", "")
    return bool(v) and bool(_NUM.match(v)) and not _YEAR.match(v)


def _header_rows(rows):
    if len(rows) < 3:
        return 1
    first = [c.strip() for c in rows[0]]
    merged = any(not c for c in first[1:]) or len({c for c in first if c}) < len([c for c in first if c])
    if not merged:
        return 1
    width = len(first)
    h = 1
    for row in rows[1:4]:
        cells = [c.strip() for c in row] + [""] * (width - len(row))
        if any(_is_num(c) for c in cells):
            break
        if cells[0]:                     # 下层表头在键列位置为空;不空说明已经是数据行
            break
        h += 1
    if h == 1 or h >= len(rows):
        return 1
    return min(h, 3)


def _fill(row, width):
    out, last = [], ""
    for i in range(width):
        v = (row[i] if i < len(row) else "").strip()
        last = v or last
        out.append(last)
    return out


def parse(rows):
    """rows: CSV 读出的行(字符串列表)。→ {"header_rows", "columns", "parts", "data", "long"}
    long 为 None,或 {"columns": [...键列, "期间", "项目", "值"], "rows": [...]}。"""
    rows = [list(r) for r in rows or []]
    if not rows:
        return {"header_rows": 0, "columns": [], "parts": [], "data": [], "long": None}
    h = _header_rows(rows)
    width = len(rows[0])
    if h == 1:
        cols = [c.strip() for c in rows[0]]
        return {"header_rows": 1, "columns": cols, "parts": [[c] for c in cols], "data": rows[1:], "long": None}
    head = rows[:h]
    # 键列:最下层表头为空、最上层有名称的那几列(地区、产线);其上层名称就是列名
    bottom = [(head[-1][i] if i < len(head[-1]) else "").strip() for i in range(width)]
    keys = [i for i in range(width) if not bottom[i] and (head[0][i] if i < len(head[0]) else "").strip()]
    filled = [_fill(r, width) for r in head[:-1]] + [bottom]
    parts = []
    for i in range(width):
        if i in keys:
            parts.append([head[0][i].strip()])
            continue
        seq: list[str] = []
        for level in filled:
            v = level[i]
            if v and (not seq or seq[-1] != v):
                seq.append(v)
        parts.append(seq or [f"c{i}"])
    columns = ["_".join(p) for p in parts]
    data = rows[h:]
    value_cols = [i for i in range(width) if i not in keys]
    long = None
    upper = ["_".join(parts[i][:-1]) for i in value_cols]
    if keys and value_cols and all(len(parts[i]) >= 2 for i in value_cols) and all(_PERIOD.search(u) for u in upper):
        name_of = lambda i: parts[i][-1]                     # noqa: E731
        out = []
        for r in data:
            r = r + [""] * (width - len(r))
            for i in value_cols:
                if r[i].strip():
                    out.append([r[k] for k in keys] + ["_".join(parts[i][:-1]), name_of(i), r[i]])
        long = {"columns": [columns[k] for k in keys] + ["期间", "项目", "值"], "rows": out}
    return {"header_rows": h, "columns": columns, "parts": parts, "data": data, "long": long}
