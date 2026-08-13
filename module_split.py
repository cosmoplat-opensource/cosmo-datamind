#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本体模块化划分 —— DR-031。

《本体智能研究报告(1.0)》阶段二(本体建模)把模块化列为**关键决策**之一:

> 本体的**模块化策略(按领域或按层次拆分)**;命名规范与注释标准的制定。

五大价值里也点到:"本体的**模块化结构**和形式化特征使其具有良好的可演进性……
当领域知识发展变化时,只需更新相应的**本体模块**,而无须重新训练整个 AI 模型。"

我们的本体是一张平图:108 个对象平铺,没有模块边界。后果很具体——
改一处不知道影响范围、想按域交付给不同团队维护无从下手、
新人打开图谱看到 108 个节点无法建立认知。

本模块给出**基于图结构的确定性划分建议**,两种策略对应报告的两种拆法:

  by_domain  按领域:连通分量 + 命名前缀聚类(dws_/dim_/fact_ 等)
  by_layer   按层次:数仓分层语义(维度层/事实层/汇总层/应用层)

设计取舍:
- **只建议,不落盘改结构**。模块边界是业务决策(哪些概念属于"销售域"要业务说了算),
  算法只能给出结构上的自然分界供人调整。自动切分会把一个错误的边界固化进本体。
- **不调 LLM**。让模型"猜"领域归属会产生看似合理实则随意的划分,
  且同一本体两次调用可能给出不同结果——模块边界必须稳定可复现。
- **孤岛单列**。不参与任何关系的对象无法由连通性归组,强行塞进某模块是编造。
"""
import re
from collections import Counter, defaultdict

# 数仓分层前缀 → 层次语义(按域拆时也用作同域信号)
_LAYER = [
    ("dim_", "维度层"), ("fact_", "事实层"), ("dws_", "汇总层"),
    ("dwd_", "明细层"), ("ods_", "贴源层"), ("agg_", "聚合层"),
    ("app_", "应用层"), ("tmp_", "临时层"),
]


def _rels(ir):
    if "links" in ir:
        return ir["links"], "source", "target"
    return ir.get("relations", []), "source_concept", "target_concept"


def _key(o, i):
    return o.get("id") or o.get("name") or f"_obj{i}"


def _layer_of(o):
    t = (o.get("table") or o.get("id") or "").lower()
    for p, name in _LAYER:
        if t.startswith(p):
            return name
    return "未分层"


def _stem(o):
    """去掉分层前缀后的词根,用作同域信号(dim_customer / fact_customer_order → customer)"""
    t = (o.get("table") or o.get("id") or "").lower()
    for p, _ in _LAYER:
        if t.startswith(p):
            t = t[len(p):]
            break
    parts = [x for x in re.split(r"[_\W]+", t) if x]
    return parts[0] if parts else t


def by_layer(ir):
    """按层次拆分:数仓分层语义。层次边界客观、无需业务确认,可直接用。"""
    groups = defaultdict(list)
    for i, o in enumerate(ir.get("objects", [])):
        groups[_layer_of(o)].append({"key": _key(o, i),
                                     "cn": o.get("cn") or o.get("name"),
                                     "table": o.get("table")})
    mods = [{"module": k, "size": len(v), "members": v} for k, v in groups.items()]
    mods.sort(key=lambda m: -m["size"])
    return mods


def by_domain(ir):
    """按领域拆分:连通分量优先,分量内再按词根聚类。

    连通分量是**结构上的自然边界**——组间无任何关系,拆开互不影响。
    大分量(如所有事实表都连到同一批维度)再按词根细分,避免"一个巨型域"。
    """
    objs = ir.get("objects", [])
    keys = [_key(o, i) for i, o in enumerate(objs)]
    meta = {_key(o, i): o for i, o in enumerate(objs)}
    rels, sk, tk = _rels(ir)

    adj = defaultdict(set)
    for r in rels:
        s, t = r.get(sk), r.get(tk)
        if s in meta and t in meta:
            adj[s].add(t)
            adj[t].add(s)

    seen, comps = set(), []
    for k in keys:
        if k in seen:
            continue
        stack, comp = [k], []
        seen.add(k)
        while stack:
            c = stack.pop()
            comp.append(c)
            for nx in adj[c]:
                if nx not in seen:
                    seen.add(nx)
                    stack.append(nx)
        comps.append(comp)

    mods, isolated = [], []
    for comp in comps:
        if len(comp) == 1 and not adj[comp[0]]:
            isolated.append(comp[0])
            continue
        # 分量内按词根聚类;词根占比最高者作模块名候选
        stems = Counter(_stem(meta[k]) for k in comp)
        top, cnt = stems.most_common(1)[0]
        name = f"{top}域" if cnt >= 2 else f"{comp[0]}域"
        mods.append({
            "module": name, "size": len(comp),
            "cohesion": round(cnt * 100.0 / len(comp), 1),   # 主词根占比:越高越内聚
            "members": [{"key": k, "cn": meta[k].get("cn") or meta[k].get("name"),
                         "table": meta[k].get("table")} for k in comp],
        })
    mods.sort(key=lambda m: -m["size"])
    if isolated:
        mods.append({
            "module": "未归组(孤岛)", "size": len(isolated), "cohesion": 0.0,
            "members": [{"key": k, "cn": meta[k].get("cn") or meta[k].get("name"),
                         "table": meta[k].get("table")} for k in isolated],
            "note": "不参与任何关系,无法由连通性归组——强行归入某模块是编造",
        })
    return mods


def suggest(ir, strategy="by_domain"):
    mods = by_layer(ir) if strategy == "by_layer" else by_domain(ir)
    total = len(ir.get("objects", []))
    biggest = mods[0]["size"] if mods else 0
    advice = []
    if biggest > max(20, total * 0.6):
        advice.append({
            "level": "warn",
            "desc": f"最大模块含 {biggest}/{total} 个对象,占比过高",
            "fix": "存在『万能枢纽』把多个域串成一片;考虑按业务过程再拆,或核查枢纽对象是否该拆分",
        })
    lone = next((m for m in mods if m["module"].startswith("未归组")), None)
    if lone and lone["size"] > total * 0.3:
        advice.append({
            "level": "warn",
            "desc": f"{lone['size']}/{total} 个对象未参与任何关系,无法归组",
            "fix": "先补关系再谈模块化——孤岛过多时模块边界不可信",
        })
    return {
        "strategy": strategy, "total_objects": total,
        "module_count": len(mods), "modules": mods, "advice": advice,
        "note": "确定性结构划分建议,不调 LLM、不落盘改结构。模块边界最终是业务决策——"
                "算法只给出结构上的自然分界供人调整,自动切分会把错误边界固化进本体",
    }
