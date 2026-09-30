#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交叉复核与清洗规则的两个接入点(DR-063):构建后自动复核,问数时套用清洗规则。

复核与清洗的方法本身在技能 ``ontology-cross-check`` 里(DR-062),本模块只负责接线:

- **构建后**:选中该技能的构建,在数据裁决之后、验收检查之前运行技能脚本,
  强冲突的已验证关系降为候选,清洗规则挂到对象的 ``evidence.cleaning``。
- **问数时**:把本体里的清洗规则变成只读连接上的临时视图(``CREATE TEMP VIEW``,
  与源表同名,SQLite 解析未限定表名时先找 temp),查询自动看到更正后的编码;
  码表、孤儿记录比例等作为说明交给模型。源库一律不改,连接关闭视图即消失。

临时视图的 SQL 只由规则的结构化字段重建,每个表名、列名都先在当前连接里核对存在,
取值一律按字面量转义;IR 里存的 ``sql`` 字段只供人读,从不执行。
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import time

SKILL = "ontology-cross-check"
_CACHE: dict = {}


def _q(name):
    return '"' + str(name).replace('"', '""') + '"'


def _lit(v):
    return "NULL" if v is None else "'" + str(v).replace("'", "''") + "'"


def _load(skill_dir, name):
    path = os.path.join(skill_dir, "scripts", name + ".py")
    key = (path, os.path.getmtime(path))
    if key not in _CACHE:
        scripts = os.path.dirname(path)
        if scripts not in sys.path:
            sys.path.append(scripts)
        spec = importlib.util.spec_from_file_location(f"_skill_{name}", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _CACHE[key] = mod
    return _CACHE[key]


def available(skill_dir):
    return bool(skill_dir) and all(os.path.isfile(os.path.join(skill_dir, "scripts", n + ".py"))
                                   for n in ("cross_check", "data_clean"))


# ── 构建后自动复核 ──────────────────────────────────────────────────────

def run_after_build(ir, db, skill_dir):
    """就地改写 ir:复核结论写进关系证据,强冲突降级,清洗规则挂到对象。返回摘要。"""
    cc, dc = _load(skill_dir, "cross_check"), _load(skill_dir, "data_clean")
    handle = cc.Db(db)
    try:
        rows, hubs = cc.check(ir, handle)
        summary = cc.summarize(rows, hubs)
        cc.apply(ir, rows)
        for o in ir.get("objects") or []:
            (o.get("evidence") or {}).pop("cleaning", None)   # 重跑时不留上一轮的规则
        bound = {t for o in ir.get("objects") or [] for t in (o.get("tables") or [o.get("table")]) if t}
        cleaning = dc.clean(handle, handle.con, cc.usable_relations(ir, rows, handle), bound)
        cc.apply_cleaning(ir, cleaning)
    finally:
        handle.con.close()
    counts: dict = {}
    for r in cleaning["rules"]:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
    summary["cleaning"] = counts
    ir.setdefault("scenario", {})["cross_check"] = {**summary, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    return summary


def build_step_text(summary):
    v = summary.get("verdicts") or {}
    by = summary.get("refuted_by") or {}
    reasons = "、".join(f"{k} {n}" for k, n in by.items())
    return (f"交叉复核 · 已验证 {summary.get('verified', 0)} 条中:强冲突降为候选 {v.get('refuted', 0)} 条"
            + (f"({reasons})" if reasons else "")
            + f" · 弱冲突待复核 {v.get('review', 0)} 条 · 有独立证据 {v.get('corroborated', 0)} 条"
            + f" · 租户键 {summary.get('tenant_or_hub_key', 0)} 条 · 按条件成立的候选 {v.get('conditional_candidate', 0)} 条")


def clean_step_text(summary):
    c = summary.get("cleaning") or {}
    names = {"codebook": "码表", "encoding_conflict": "编码冲突", "outlier_code": "异常编码值",
             "coherent_dangling_relation": "有孤儿记录但语义成立", "orphan_reference": "孤儿记录",
             "empty_shell": "迁移遗留空表", "comment_code_mismatch": "注释编码不符"}
    parts = [f"{names.get(k, k)} {n}" for k, n in c.items()]
    return "数据清洗规则 · " + (" · ".join(parts) if parts else "未发现需要处理的问题") + " · 已挂到对应对象,问数时生效"


# ── 问数时套用 ─────────────────────────────────────────────────────────

def rules_by_table(ir):
    """从本体对象收集清洗规则,按(小写)表名分组并去重。"""
    out: dict = {}
    seen = set()
    for o in (ir or {}).get("objects") or []:
        for r in ((o.get("evidence") or {}).get("cleaning") or []):
            if not isinstance(r, dict):
                continue
            sig = json.dumps(r, sort_keys=True, ensure_ascii=False, default=str)
            if sig in seen:
                continue
            seen.add(sig)
            t = str(r.get("table") or "").lower()
            if t:
                out.setdefault(t, []).append(r)
    return out


def _cols(con, schema, table):
    try:
        return [r[1] for r in con.execute(f"PRAGMA {schema}.table_info({_q(table)})")]
    except Exception:
        return []


def _find(cols, name):
    return next((c for c in cols if c.lower() == str(name or "").lower()), None)


def _recode(con, schema, table, cols, rule):
    """校验后重建一条异常编码值规则的列表达式;结构不合法或任何标识符对不上就放弃这条规则。"""
    col = _find(cols, rule.get("column"))
    cat = rule.get("category") or {}
    groups = [g for g in (rule.get("groups") or []) if isinstance(g, dict) and "category" in g and "expected" in g]
    if not col or not groups or not isinstance(cat, dict):
        return None, None
    via, cond = cat.get("via"), cat.get("condition")
    if (via and not isinstance(via, dict)) or (cond and not isinstance(cond, dict)):
        return None, None
    if via:
        ptab = str(cat.get("table") or "")
        pschema = schema if _cols(con, schema, ptab) else "main"
        pcols = _cols(con, pschema, ptab)
        pcat, pkey = _find(pcols, cat.get("column")), _find(pcols, via.get("parent_key"))
        ckey = _find(cols, via.get("child_key"))
        if not (pcat and pkey and ckey):
            return None, None
        probe = (f"(SELECT p.{_q(pcat)} FROM {pschema}.{_q(ptab)} p "
                 f"WHERE p.{_q(pkey)}=src.{_q(ckey)} LIMIT 1)")
    else:
        ccat = _find(cols, cat.get("column"))
        if not ccat:
            return None, None
        probe = f"src.{_q(ccat)}"
    guard = ""
    if cond:
        ccol = _find(cols, cond.get("column"))
        if not ccol:
            return None, None
        guard = f"src.{_q(ccol)}={_lit(cond.get('value'))} AND "
    cases = " ".join(f"WHEN {guard}{probe}={_lit(g['category'])} THEN {_lit(g['expected'])}" for g in groups[:20])
    return col, f"CASE {cases} ELSE src.{_q(col)} END"


def apply_views(con, rules, sql):
    """在只读连接上为有异常编码值规则的表建同名临时视图。返回 (改写后的 sql, 已生效说明)。

    主库表:临时视图与表同名,未限定的表名自动解析到视图。
    上传库表(以 up.表 访问):视图另起名,并把 SQL 里的 up.表 改指向视图。
    """
    applied = []
    low_sql = (sql or "").lower()
    for table, rs in (rules or {}).items():
        outl = [r for r in rs if r.get("kind") == "outlier_code"]
        if not outl or table not in low_sql:
            continue
        schema = "main" if _cols(con, "main", table) else ("up" if _cols(con, "up", table) else None)
        if not schema:
            continue
        cols = _cols(con, schema, table)
        real = next((r.get("table") for r in outl), table)
        exprs, used = {}, []
        for r in outl:
            col, expr = _recode(con, schema, real, cols, r)
            if col and col not in exprs:
                exprs[col] = expr
                used.append(r)
        if not exprs:
            continue
        view = real if schema == "main" else f"_clean_up_{real}"
        select = ", ".join(f"{exprs[c]} AS {_q(c)}" if c in exprs else f"src.{_q(c)}" for c in cols)
        try:
            con.execute(f"CREATE TEMP VIEW {_q(view)} AS SELECT {select} FROM {schema}.{_q(real)} AS src")
        except Exception:
            continue
        if schema == "up":
            sql = re.sub(r'\bup\s*\.\s*"?' + re.escape(real) + r'"?(?![\w"])', _q(view), sql, flags=re.I)
        for r in used:
            applied.append({"table": real, "column": r.get("column"), "rows": r.get("outlier_rows"),
                            "by": r.get("determined_by")})
    return sql, applied


def notes_for_sql(rules, sql):
    """查询用到的表上若有高比例孤儿记录或迁移遗留空表,给出提示(不改结果)。"""
    low = (sql or "").lower()
    notes = []
    for table, rs in (rules or {}).items():
        if not re.search(r"(?<![\w])" + re.escape(table) + r"(?![\w])", low):
            continue
        for r in rs:
            if r.get("kind") == "orphan_reference" and r.get("severity") == "high":
                notes.append(f"{r['table']}.{r['column']} 指向 {r['parent']} 的引用有 "
                             f"{round(100 * (r.get('orphan_rate') or 0))}% 为孤儿记录(父表中无对应行),"
                             "按关联取数时这部分不会出现,汇总可能偏小")
            elif r.get("kind") == "empty_shell" and table in [t.lower() for t in r.get("empty", [])]:
                notes.append(f"{table} 是迁移留下的空表,有数据的是 {'、'.join(r.get('populated', []))}")
    return notes[:4]


def context_text(rules, tables, cap=1800):
    """给模型的清洗说明:锚定表上的码表、已自动更正的编码、孤儿记录与空表提示。"""
    want = {str(t).lower().split(".", 1)[-1] for t in tables or []}
    lines = []
    for table in sorted(want):
        for r in rules.get(table, []):
            k = r.get("kind")
            if k == "codebook":
                labels = "、".join(f"{c}={lab}" for c, lab in list((r.get("labels") or {}).items())[:14])
                lines.append(f"- {r['table']}.{r['column']} 取值含义:{labels}")
            elif k == "outlier_code":
                lines.append(f"- {r['table']}.{r['column']} 有 {r.get('outlier_rows')} 行按「{r.get('determined_by')}」"
                             "自动更正(查询时已生效,直接用该列即可)")
            elif k == "orphan_reference" and r.get("severity") == "high":
                lines.append(f"- {r['table']}.{r['column']} → {r['parent']} 有 "
                             f"{round(100 * (r.get('orphan_rate') or 0))}% 为孤儿记录,关联后行数会减少")
            elif k == "empty_shell":
                lines.append(f"- {r['table']} 同名表中有数据的是 {'、'.join(r.get('populated', []))},不要用空表")
    if not lines:
        return ""
    text = "[数据清洗规则(由交叉复核自动生成,按字段实际使用的码表解释取值)]\n" + "\n".join(lines)
    return text[:cap]
