#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""概念画像:把本体对象预计算成可检索的一段业务描述 —— DR-055。

运行时按关键词在零散表列里找对象,召回依赖问句恰好命中列名。画像把一个对象的
名称、别名、定义、属性中文名、沿 verified/asserted 关系可达的邻居及动词、绑定的指标
预先聚合成一段文本,检索时先命中画像再取子图。全部从 IR 确定性推导,可回放;
不引入向量索引,离线可用(向量索引可作为可选增强叠加在同一份画像上)。
"""
from __future__ import annotations

import re

import metric_contract as MC
from ir_shape import obj_names, rels as _rels

_STRONG = ("verified", "asserted")
_SPLIT = re.compile(r"[\s,，。？?！!、;；:：()（）\[\]【】\"'“”]+")


def _key(o, idx):
    return o.get("id") or o.get("name") or o.get("cn") or f"_obj{idx}"


def _table(o):
    return o.get("table") or ((o.get("tables") or [None])[0]) or ""


def build(ir):
    """IR → 画像列表(按对象键排序,确定性)。"""
    objs = (ir or {}).get("objects") or []
    by_key = {_key(o, i): o for i, o in enumerate(objs)}
    rels, sk, tk = _rels(ir or {})
    neigh = {}
    for r in rels:
        if r.get("status") not in _STRONG:
            continue
        s, t = r.get(sk), r.get(tk)
        if not (s in by_key and t in by_key):
            continue
        verb = r.get("verb") or "关联"
        neigh.setdefault(s, []).append({"object": t, "cn": by_key[t].get("cn") or t, "verb": verb,
                                        "direction": "out", "status": r.get("status")})
        neigh.setdefault(t, []).append({"object": s, "cn": by_key[s].get("cn") or s, "verb": verb,
                                        "direction": "in", "status": r.get("status")})
    metrics_by_table = {}
    for arr in ((ir or {}).get("metric_layers") or {}).values() if isinstance((ir or {}).get("metric_layers"), dict) else []:
        for m in (arr or []):
            if isinstance(m, dict) and m.get("table"):
                metrics_by_table.setdefault(str(m["table"]).lower(), []).append(
                    {"name": m.get("name"), "status": m.get("status") or ("candidate" if m.get("candidate", True) else ""),
                     "caliber": MC.describe(m) if MC.is_contract(m) else ""})
    out = []
    for i, o in enumerate(objs):
        k = _key(o, i)
        cols = [{"col": a.get("col"), "cn": a.get("cn") or ""} for a in (o.get("attrs") or []) if a.get("col")]
        prof = {
            "key": k, "name": o.get("name") or k, "cn": o.get("cn") or "", "kind": o.get("kind") or "",
            "table": _table(o), "aliases": [a for a in (o.get("aliases") or []) if isinstance(a, str)],
            "definition": o.get("definition") or "",
            "columns": cols[:40],
            "relations": sorted(neigh.get(k, []), key=lambda x: (x["object"], x["direction"]))[:30],
            "metrics": metrics_by_table.get(str(_table(o)).lower(), [])[:20],
        }
        words = obj_names(o) + [c["cn"] for c in cols if c["cn"]] + [c["col"] for c in cols]
        words += [f"{n['verb']}{n['cn']}" for n in prof["relations"]] + [m["name"] for m in prof["metrics"] if m["name"]]
        prof["text"] = " ".join(x for x in words if x) + " " + prof["definition"]
        out.append(prof)
    return sorted(out, key=lambda p: str(p["key"]))


def search(profiles, query, limit=5):
    """关键词检索:名称/别名/中文名完整出现在问句 → 5 分;列名或列中文名 → 2 分;
    关系动词+邻居名、指标名、定义片段 → 1 分。同分按键名排序,结果可回放。"""
    q = (query or "").strip()
    if not q:
        return []
    ql = q.lower()
    toks = [t for t in _SPLIT.split(q) if len(t) >= 2]
    scored = []
    for p in profiles:
        score, hits = 0, []
        for n in [p["name"], p["cn"], p["table"]] + p["aliases"]:
            if n and len(n) >= 2 and n.lower() in ql:
                score += 5; hits.append(n)
        for c in p["columns"]:
            for n in (c["cn"], c["col"]):
                if n and len(n) >= 2 and n.lower() in ql and n not in hits:
                    score += 2; hits.append(n)
        for m in p["metrics"]:
            if m["name"] and len(m["name"]) >= 2 and m["name"].lower() in ql and m["name"] not in hits:
                score += 1; hits.append(m["name"])
        for r in p["relations"]:
            if r["cn"] and len(r["cn"]) >= 2 and r["cn"].lower() in ql and r["cn"] not in hits:
                score += 1; hits.append(r["cn"])
        for t in toks:
            if t.lower() in p["definition"].lower() and t not in hits:
                score += 1; hits.append(t)
        if score:
            scored.append((score, str(p["key"]), hits, p))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [{"score": s, "hits": h, **{k: v for k, v in p.items() if k != "text"}} for s, _, h, p in scored[:limit]]


def render(profile):
    """一段可直接喂给模型或人的画像文本(确定性)。"""
    p = profile
    lines = [f"对象 {p['cn'] or p['name']}({p['name']}{',表 ' + p['table'] if p['table'] else ''})"]
    if p["aliases"]:
        lines.append("别名:" + "、".join(p["aliases"]))
    if p["definition"]:
        lines.append("定义:" + p["definition"])
    if p["columns"]:
        lines.append("属性:" + "、".join((c["cn"] + "(" + c["col"] + ")") if c["cn"] else c["col"] for c in p["columns"][:20]))
    if p["relations"]:
        lines.append("关系:" + "; ".join(
            (f"{p['cn'] or p['name']} {r['verb']} {r['cn']}" if r["direction"] == "out" else f"{r['cn']} {r['verb']} {p['cn'] or p['name']}")
            + f"[{r['status']}]" for r in p["relations"][:10]))
    if p["metrics"]:
        lines.append("指标:" + "; ".join(f"{m['name']}[{m['status']}]" + (f" {m['caliber']}" if m["caliber"] else "") for m in p["metrics"][:10]))
    return "\n".join(lines)
