#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""概念漂移与关系断裂检测 —— DR-025。

回答一个 CQ 核验回答不了的问题:**本体还对不对得上数据源**。

本体建成之日与数据源是一致的,但库会继续演进——表被改名、列被删、
主键换了名字。此时本体不会报错,只会在问数时静默产出错误 SQL 或空结果:
本体说 `dim_customer.cust_id` 存在,库里已经没有,SQL 照样生成、照样执行失败,
而失败原因被埋在一句"查询出错"里。

检测全程为确定性比对(IR 声明 vs 库内 schema),不调 LLM、不猜测意图:

  table_missing   对象绑定的表在数据源中已不存在(概念漂移:实体没了)
  column_missing  属性绑定的列已不存在(概念漂移:属性没了)
  pk_missing      对象声明的主键列已不存在(锚点断裂,影响关系可信度)
  relation_broken 关系证据引用的父/子键列已不存在(关系断裂)

设计取舍:
- **只报事实,不自动修复**。漂移的正解可能是改本体、也可能是数据源回滚,
  这是人的判断;自动删对象会悄悄抹掉建模成果,违反可撤销原则。
- **大小写不敏感比对**。SQLite 标识符大小写不敏感,IR 里 `DWS_X` 与库里
  `dws_x` 是同一张表,若按字面比对会产出整片假阳性。
- **区分"缺失"与"未绑定"**。对象没有 table 字段(如纯概念对象)不算漂移,
  跳过而非报错——报告里的语义层本就允许存在不落库的抽象概念。
"""
import sqlite3


def _schema(db_path):
    """读数据源 schema → {表名小写: {列名小写}};附带原名映射用于报告可读性。"""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        tabs = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')")]
        out, disp = {}, {}
        for t in tabs:
            cols = {}
            for r in con.execute(f'PRAGMA table_info("{t}")'):
                cols[str(r[1]).lower()] = r[1]
            out[t.lower()] = cols
            disp[t.lower()] = t
        return out, disp
    finally:
        con.close()


def _rels(ir):
    if "links" in ir:
        return ir["links"], "source", "target"
    return ir.get("relations", []), "source_concept", "target_concept"


def check(ir, db_path):
    """比对 IR 与数据源,返回漂移报告。"""
    try:
        schema, disp = _schema(db_path)
    except Exception as e:
        return {"error": f"数据源不可读: {e}", "checked": False}

    issues = []
    obj_table = {}          # 对象键 → 表名(小写),供关系检测复用
    n_obj_bound = n_col_checked = 0

    for i, o in enumerate(ir.get("objects", [])):
        key = o.get("id") or o.get("name") or f"_obj{i}"
        cn = o.get("cn") or o.get("name") or key
        t = (o.get("table") or "").strip()
        if not t:
            continue                     # 未绑表的纯概念对象不参与漂移检测
        n_obj_bound += 1
        tl = t.lower()
        obj_table[key] = tl
        if tl not in schema:
            issues.append({
                "type": "table_missing", "severity": "high",
                "object": key, "cn": cn, "table": t,
                "desc": f"对象「{cn}」绑定的表 {t} 在数据源中不存在",
                "fix": "确认表是否改名/下线:若改名则更新对象绑定,若下线则下架该对象及其关系",
            })
            continue

        cols = schema[tl]
        pk = (o.get("pk") or "").strip()
        if pk and pk.lower() not in cols:
            issues.append({
                "type": "pk_missing", "severity": "high",
                "object": key, "cn": cn, "table": t, "column": pk,
                "desc": f"对象「{cn}」的主键列 {t}.{pk} 已不存在",
                "fix": "主键是关系裁决的锚点,须先更正主键绑定再复核相关关系",
            })
        for a in (o.get("attrs") or []):
            c = (a.get("col") or "").strip()
            if not c:
                continue
            n_col_checked += 1
            if c.lower() not in cols:
                issues.append({
                    "type": "column_missing", "severity": "medium",
                    "object": key, "cn": cn, "table": t, "column": c,
                    "desc": f"属性「{a.get('cn') or c}」绑定的列 {t}.{c} 已不存在",
                    "fix": "确认列是否改名/删除:改名则更新属性绑定,删除则移除该属性",
                })

    rels, sk, tk = _rels(ir)
    for r in rels:
        s, t = r.get(sk), r.get(tk)
        ev = r.get("evidence") or {}
        ck, pk2 = (ev.get("child_key") or "").strip(), (ev.get("parent_key") or "").strip()
        if not (ck or pk2):
            continue                      # 无列级证据的关系(如人工断言)不在本检测范围
        for who, col, obj in ((("子", ck, s)), (("父", pk2, t))):
            if not col or obj not in obj_table:
                continue
            tl = obj_table[obj]
            if tl in schema and col.lower() not in schema[tl]:
                issues.append({
                    "type": "relation_broken", "severity": "high",
                    "relation": f"{s}->{t}", "verb": r.get("verb"),
                    "table": disp.get(tl, tl), "column": col,
                    "desc": f"关系 {s}→{t} 的{who}键 {disp.get(tl, tl)}.{col} 已不存在",
                    "fix": "关系证据失效:须重新做取值重叠裁决,或降级为 candidate 待人审",
                })

    cnt = {}
    for it in issues:
        cnt[it["type"]] = cnt.get(it["type"], 0) + 1
    total = n_obj_bound + n_col_checked + len(rels)
    return {
        "checked": True,
        "healthy": not issues,
        "counts": cnt,
        "issues": issues,
        "scanned": {"objects_bound": n_obj_bound, "columns": n_col_checked, "relations": len(rels)},
        # 一致率:未出问题的检查点占比,给出量化结构一致性而非只报有无
        "consistency": round((total - len(issues)) * 100.0 / total, 1) if total else 100.0,
        "note": "确定性 schema 比对,不调 LLM;只报事实不自动修复——"
                "漂移的正解可能是改本体也可能是数据源回滚,须人判断",
    }


def gaps_from(report):
    """漂移项转缺口条目,与 CQ 缺口同格式,统一回流人审队列。"""
    return [{"type": "drift_" + it["type"], "desc": it["desc"], "fix": it["fix"]}
            for it in report.get("issues", [])]
