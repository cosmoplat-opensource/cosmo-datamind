#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""双盲意图检测 —— DR-026。

《本体智能研究报告》电力案例:将复杂分析标准化为「获取对象→提取数据→计算指标」
三步,并**配套双盲检测机制确保查询意图准确性**。

本系统原有链路是单通道:问句 → LLM 生成 SQL → 口径闸 → 执行。
口径闸管的是「SQL 合不合规」(表在不在白名单、JOIN 键落不落本体),
管不了「**SQL 答的是不是用户问的那件事**」——一条完全合规的 SQL
可以查一张完全不相干的表,闸放行,用户拿到一个像模像样的错答案。
这正是排障表里的第一类症状:答非所问。

「双盲」的实质是两条**互不透传**的通道各自判断意图,再比对:

  通道 A(声明意图):只看自然语言问句 → 锚定本体对象 + 命中指标词
  通道 B(实际意图):只看生成的 SQL   → 反解 FROM/JOIN 的表 → 映射回对象

两条通道都不调 LLM,也都不看对方的结果——用模型验模型是同源偏差,
"看起来对"会被判成"对"。确定性反解则可回放、可解释。

判定:
  aligned    B 覆盖了 A 声明的对象(SQL 确实在查用户问的东西)
  partial    B 覆盖了一部分(可能漏了某个维度)
  mismatch   B 与 A 完全不相交(高度可疑:答非所问)
  unknown    A 未能锚定任何对象(问句太泛,无从比对——不做判断,不冒充通过)
"""
import re

_IDENT = r"[A-Za-z_][A-Za-z0-9_.\"`\[\]]*"


def _obj_names(o):
    out = []
    for k in ("id", "name", "cn", "table"):
        v = o.get(k)
        if isinstance(v, str) and v.strip():
            out.append(v.strip())
    return out


def declared_intent(question, ir):
    """通道 A:只看问句。确定性锚定本体对象 + 抽取指标词。"""
    q = (question or "").lower()
    objs, seen = [], set()
    for o in ir.get("objects", []):
        key = o.get("id") or o.get("name")
        if not key or key in seen:
            continue
        for nm in _obj_names(o):
            n = nm.lower()
            # 与 CQ 锚定同一从严策略:中文≥2字、英文≥3字符,完整出现才算命中
            if (len(n) >= 2 if re.search(r"[一-鿿]", n) else len(n) >= 3) and n in q:
                objs.append({"key": key, "matched": nm,
                             "cn": o.get("cn") or o.get("name") or key,
                             "table": (o.get("table") or "").lower()})
                seen.add(key)
                break
    metrics = []
    for o in ir.get("objects", []):
        for m in (o.get("supported_metrics") or []):
            if m and len(m) >= 2 and m.lower() in q and m not in metrics:
                metrics.append(m)
    return {"objects": objs, "metrics": metrics}


def actual_intent(sql, ir):
    """通道 B:只看 SQL。反解 FROM/JOIN 的表,映射回本体对象。

    只认真实表:剔除 CTE 名与子查询别名——把 CTE 当成表会让「SQL 查了什么」失真。
    """
    s = sql or ""
    ctes = {m.group(1).lower() for m in
            re.finditer(r"(?:\bwith\b|,)\s*(%s)\s+as\s*\(" % _IDENT, s, re.I)}
    tabs = set()
    for m in re.finditer(r"\b(?:from|join)\s+(%s)" % _IDENT, s, re.I):
        t = m.group(1).strip().strip('"`[]').lower()
        t = t.split(".")[-1] if t.startswith("up.") else t   # 上传库前缀不影响表名比对
        if t and t not in ctes:
            tabs.add(t)
    hit, seen = [], set()
    for o in ir.get("objects", []):
        t = (o.get("table") or "").lower()
        key = o.get("id") or o.get("name")
        if t and t in tabs and key not in seen:
            hit.append({"key": key, "cn": o.get("cn") or o.get("name") or key, "table": t})
            seen.add(key)
    return {"tables": sorted(tabs), "objects": hit,
            "unmapped": sorted(t for t in tabs
                               if t not in {o["table"] for o in hit})}


def cross_check(question, sql, ir):
    """双盲比对。两通道各自独立产出后才比,任一通道不参考对方中间结果。"""
    a = declared_intent(question, ir)
    b = actual_intent(sql, ir)
    ka = {o["key"] for o in a["objects"]}
    kb = {o["key"] for o in b["objects"]}

    if not ka:
        return {"verdict": "unknown", "declared": a, "actual": b,
                "reason": "问句未锚定到本体对象,无从比对意图——不做判断",
                "advice": ""}
    inter = ka & kb
    missed = [o for o in a["objects"] if o["key"] not in kb]
    if not inter:
        return {"verdict": "mismatch", "declared": a, "actual": b,
                "overlap": 0.0, "missed": missed,
                "reason": "SQL 查询的表与问句提到的业务对象完全不相交",
                "advice": "高度可疑的答非所问:建议向用户澄清,或重新生成 SQL,"
                          "不应直接把结果当答案给出"}
    cov = round(len(inter) * 100.0 / len(ka), 1)
    if missed:
        return {"verdict": "partial", "declared": a, "actual": b,
                "overlap": cov, "missed": missed,
                "reason": "问句提到的部分对象未出现在 SQL 中: "
                          + "、".join(o["cn"] for o in missed),
                "advice": "可能遗漏了某个维度,答案需标注覆盖范围"}
    return {"verdict": "aligned", "declared": a, "actual": b,
            "overlap": cov, "missed": [],
            "reason": "SQL 覆盖了问句提到的全部业务对象", "advice": ""}


def step_of(res):
    """转成问数执行记录里的一个步骤条目(与既有 steps 结构一致)。"""
    ok = res["verdict"] in ("aligned", "unknown")
    label = {"aligned": "意图一致", "partial": "意图部分一致",
             "mismatch": "意图不一致(疑似答非所问)", "unknown": "意图无法比对"}[res["verdict"]]
    info = label + " · " + res["reason"]
    if res.get("advice"):
        info += " · " + res["advice"]
    return {"step": "intent_crosscheck", "ok": ok, "info": info[:300]}
