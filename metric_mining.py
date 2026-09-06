#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""指标反解:从三类工程产物里抽取指标候选与参照 —— DR-054。

没有成熟指标体系时,指标不该由模型凭空提议。三个入口都是已经在用的东西:
  ① 历史 SQL(视图 / ETL / 上传的 .sql 文件):聚合表达式即原子指标,GROUP BY 即维度;
  ② 看板与指标口径表(Excel 反解出的名称 / 说明 / 计算逻辑 / 维度 / 来源);
  ③ 已沉淀的问数 SQL 与参考基准 —— 它们是被真实查询验证过的口径,天然是参照。

本模块只做确定性文本解析(正则,不调模型),产物一律 ``candidate`` 且带 ``provenance``;
能否升为 ``verified`` 由 ``metric_contract.adjudicate`` 执行核验决定。含 JOIN 的语句
不产出契约(v1 契约是单实体的),只登记为「待人工拆解」,不猜口径。
"""
from __future__ import annotations

import re

import metric_contract as MC

_AGG_RE = re.compile(
    r"\b(SUM|COUNT|AVG|MIN|MAX)\s*\(\s*(DISTINCT\s+)?(\*|`?\"?[A-Za-z_][A-Za-z0-9_]*\"?`?)\s*\)"
    r"(?:\s+AS\s+(\"[^\"]+\"|`[^`]+`|[A-Za-z_一-鿿][A-Za-z0-9_一-鿿]*))?",
    re.I)
_FROM_RE = re.compile(r"\bFROM\s+(`?\"?[A-Za-z_][A-Za-z0-9_.]*\"?`?)(?:\s+(?:AS\s+)?([A-Za-z_][A-Za-z0-9_]*))?", re.I)
_JOIN_RE = re.compile(r"\bJOIN\b", re.I)
_GROUP_RE = re.compile(r"\bGROUP\s+BY\s+(.+?)(?:\bORDER\b|\bHAVING\b|\bLIMIT\b|;|$)", re.I | re.S)
_WHERE_RE = re.compile(r"\bWHERE\s+(.+?)(?:\bGROUP\b|\bORDER\b|\bHAVING\b|\bLIMIT\b|;|$)", re.I | re.S)
_PRED_RE = re.compile(
    r"^\s*`?\"?([A-Za-z_][A-Za-z0-9_]*)\"?`?\s*(=|!=|<>|>=|<=|>|<)\s*('(?:[^']|'')*'|-?\d+(?:\.\d+)?)\s*$")
_TIME_BUCKET_RE = re.compile(r"substr\(\s*`?\"?([A-Za-z_][A-Za-z0-9_]*)\"?`?\s*,\s*1\s*,\s*(4|7|10)\s*\)", re.I)
_SQL_KW = {"select", "from", "where", "group", "order", "by", "as", "on", "and", "or", "not", "null"}
_DATE_HINT = re.compile(r"date|time|day|month|dt$|_at$", re.I)


def _strip(ident):
    return ident.strip().strip('`"').split(".")[-1]


def _clean_sql(text):
    text = re.sub(r"--[^\n]*", " ", text or "")
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return text


def _statements(text):
    return [s.strip() for s in _clean_sql(text).split(";") if s.strip() and re.search(r"\bSELECT\b", s, re.I)]


def _filters(where):
    """只接受「AND 连接的简单谓词」;含 OR / 子查询 / 函数的条件不猜,整段放弃并如实标注。"""
    if not where:
        return [], False
    if re.search(r"\bOR\b|\(|\bIN\b|\bLIKE\b|\bBETWEEN\b", where, re.I):
        return [], True
    out = []
    for part in re.split(r"\bAND\b", where, flags=re.I):
        m = _PRED_RE.match(part)
        if not m:
            return [], True
        col, op, lit = m.group(1), m.group(2), m.group(3)
        op = "!=" if op == "<>" else op
        val = lit[1:-1].replace("''", "'") if lit.startswith("'") else (float(lit) if "." in lit else int(lit))
        out.append({"col": col, "op": op, "value": val})
    return out, False


def parse_sql(text, source, tab_cols=None):
    """一段 SQL 文本 → 指标候选列表 + 跳过项。``tab_cols`` 给出时只保留表存在的候选。"""
    cands, skipped = [], []
    known = {t.lower(): t for t in (tab_cols or {})}
    for idx, st in enumerate(_statements(text)):
        stmt_src = f"{source}#{idx + 1}"
        if _JOIN_RE.search(st):
            skipped.append({"source": stmt_src, "reason": "含 JOIN,契约 v1 只覆盖单实体指标,留待人工拆解"})
            continue
        fm = _FROM_RE.search(st)
        if not fm:
            continue
        table = _strip(fm.group(1))
        if known and table.lower() not in known:
            skipped.append({"source": stmt_src, "reason": f"表 {table} 不在当前数据源中"})
            continue
        if known:
            table = known[table.lower()]
        aggs = list(_AGG_RE.finditer(st))
        if not aggs:
            continue
        gm = _GROUP_RE.search(st)
        dims, grain, tcol = [], None, ""
        if gm:
            for g in gm.group(1).split(","):
                g = g.strip()
                tb = _TIME_BUCKET_RE.search(g)
                if tb:
                    tcol, grain = tb.group(1), {"4": "year", "7": "month", "10": "day"}[tb.group(2)]
                elif re.match(r"^\d+$", g):
                    continue                      # GROUP BY 1 这类序号无法还原列,交给参照比对
                elif re.match(r"^`?\"?[A-Za-z_][A-Za-z0-9_]*\"?`?$", g):
                    dims.append(_strip(g))
        if gm and not tcol:
            # GROUP BY 1 这类序号写法:时间分桶表达式只出现在 SELECT 列表里
            tb = _TIME_BUCKET_RE.search(st)
            if tb:
                tcol, grain = tb.group(1), {"4": "year", "7": "month", "10": "day"}[tb.group(2)]
        wm = _WHERE_RE.search(st)
        filters, unparsed = _filters(wm.group(1).strip() if wm else "")
        for am in aggs:
            fn, distinct, col, alias = am.group(1).lower(), bool(am.group(2)), _strip(am.group(3)), am.group(4)
            agg = "count_distinct" if (fn == "count" and distinct) else fn
            if col == "*":
                if fn != "count":
                    continue
                col = ""
            name = _strip(alias) if alias else (f"{col}_{agg}" if col else f"{table}_count")
            if name.lower() in _SQL_KW:
                name = f"{col}_{agg}"
            if not tcol:
                tcol = _guess_time_col(table, tab_cols)
            cand = {
                "id": f"mine.{table}.{col or 'rows'}.{agg}",
                "name": name, "layer": "atomic", "table": table,
                "measure": {"col": col, "agg": agg},
                "filters": filters, "dimensions": dims,
                "time": {"col": tcol, "grain": [grain] if grain else (["month"] if tcol else [])},
                "status": "candidate",
                "provenance": [{"kind": "sql", "source": stmt_src, "snippet": st[:300]}],
                "references": [{"kind": "sql", "sql": st, "source": stmt_src,
                                "dimensions": dims, "grain": grain}],
            }
            if unparsed:
                cand["provenance"][0]["note"] = "WHERE 含无法确定性解析的条件,未作为过滤写入契约"
                cand["references"] = []           # 过滤条件不完整时,原 SQL 不能作为该契约的参照
            cands.append(cand)
    return cands, skipped


def _guess_time_col(table, tab_cols):
    if not tab_cols:
        return ""
    cols = tab_cols.get(table) or []
    names = [c[0] if isinstance(c, (list, tuple)) else c for c in cols]
    types = {(c[0] if isinstance(c, (list, tuple)) else c): (c[1] if isinstance(c, (list, tuple)) and len(c) > 1 else "")
             for c in cols}
    for n in names:
        if "DATE" in str(types.get(n, "")).upper():
            return n
    for n in names:
        if _DATE_HINT.search(n or ""):
            return n
    return ""


def parse_glossary(rows, source="dashboard"):
    """看板 / 口径表反解出的行 → 未绑定的指标候选(名称 / 说明 / 计算逻辑 / 维度)。

    这类候选绑定不到表列时不可编译,保留为 candidate 供人工绑定;含运算符的计算逻辑
    记为派生指标并保留 formula 原文,不改写。
    """
    out = []
    for i, r in enumerate(rows or []):
        if not isinstance(r, dict):
            continue
        name = str(r.get("name") or r.get("指标名称") or "").strip()
        if not name or len(name) > 40:
            continue
        formula = str(r.get("formula") or r.get("计算逻辑") or "").strip()
        derived = bool(re.search(r"[+\-*/÷×%]", formula)) and len(formula) > 3
        out.append({
            "id": f"glossary.{i + 1}", "name": name,
            "layer": "derived" if derived else "atomic",
            "desc": str(r.get("desc") or r.get("指标说明") or "")[:300],
            "formula": formula[:300],
            "measure": {"col": "", "agg": ""},
            "dimensions": [], "status": "candidate",
            "provenance": [{"kind": "dashboard", "source": f"{source}#{i + 1}",
                            "dimension": str(r.get("dimension") or r.get("业务维度") or "")[:60],
                            "origin": str(r.get("source") or r.get("来源") or "")[:60]}],
            "references": [],
        })
    return out


def from_qa_skills(store, tab_cols=None):
    """沉淀技能里的已验证 SQL → 候选(参照即该 SQL)。"""
    cands, skipped = [], []
    for sk in (store or []):
        if not isinstance(sk, dict):
            continue
        q = str(sk.get("question") or "")[:60]
        for i, a in enumerate(sk.get("analyses") or []):
            sql = (a or {}).get("sql") if isinstance(a, dict) else None
            if not sql:
                continue
            c, s = parse_sql(sql, f"qa_skill:{q}[{i + 1}]", tab_cols)
            cands += c
            skipped += s
    return cands, skipped


def from_gold_set(items, tab_cols=None):
    """参考基准的 gold_sql → 候选(带 gold 数值参照;它们是经人工校准的口径)。"""
    cands, skipped = [], []
    for it in (items or []):
        if not isinstance(it, dict) or not it.get("gold_sql"):
            continue
        c, s = parse_sql(it["gold_sql"], f"gold:{it.get('id', '?')}", tab_cols)
        for x in c:
            if it.get("gold") is not None and not x.get("dimensions"):
                x["references"].append({"kind": "value", "value": it["gold"], "tol": float(it.get("tol") or 0.01) or 0.01,
                                        "source": f"gold:{it.get('id', '?')}"})
        cands += c
        skipped += s
    return cands, skipped


def _key(c):
    m = c.get("measure") or {}
    return (str(c.get("table") or "").lower(), str(m.get("col") or "").lower(), m.get("agg") or "",
            tuple(sorted((f["col"], f["op"], str(f["value"])) for f in (c.get("filters") or []))))


def collect(tab_cols=None, sql_texts=None, glossary_rows=None, qa_skills=None, gold_items=None):
    """汇总三个入口,按(表,列,聚合,过滤)去重;同口径的参照合并到同一候选。

    返回 {"candidates": [契约...], "skipped": [...], "sources": 计数}。
    """
    cands, skipped, sources = [], [], {"sql": 0, "dashboard": 0, "qa_skill": 0, "gold": 0}
    for name, text in (sql_texts or {}).items():
        c, s = parse_sql(text, f"file:{name}", tab_cols)
        sources["sql"] += len(c); cands += c; skipped += s
    g = parse_glossary(glossary_rows)
    sources["dashboard"] += len(g); cands += g
    c, s = from_qa_skills(qa_skills, tab_cols)
    sources["qa_skill"] += len(c); cands += c; skipped += s
    c, s = from_gold_set(gold_items, tab_cols)
    sources["gold"] += len(c); cands += c; skipped += s
    merged, index = [], {}
    for c in cands:
        if not (c.get("measure") or {}).get("agg"):
            merged.append(c)
            continue
        k = _key(c)
        if k in index:
            tgt = merged[index[k]]
            tgt["provenance"] = MC._merge_prov(tgt.get("provenance"), c.get("provenance"))
            for r in c.get("references") or []:
                if r not in tgt["references"]:
                    tgt["references"].append(r)
            if not tgt.get("dimensions") and c.get("dimensions"):
                tgt["dimensions"] = c["dimensions"]
            continue
        index[k] = len(merged)
        merged.append(c)
    return {"candidates": merged, "skipped": skipped, "sources": sources}
