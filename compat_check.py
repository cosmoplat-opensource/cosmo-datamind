#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本体向后兼容性检查 —— DR-031。

《本体智能研究报告(1.0)》阶段六:"本体版本管理与**向后兼容性保障**";
阶段五:"本体版本管理(建立版本发布与回滚机制)"。

本体是**语义契约**:问数靠它召回表与口径、规则靠它锚定对象、动作靠它绑定表、
穿透链路靠它连通。改本体不是改一份文档,是改一份被多方消费的契约——
删掉一个对象,可能让三条规则失效、两个动作绑不到表、一条链路断开,
而这些直到线上报错才会被发现。

本模块回答改本体前必须先问的问题:**这次变更会破坏什么**。

两层判断:
  ① 结构 diff —— 相比基线,删了什么、改了什么、降级了什么
  ② 下游影响 —— 这些变更命中了哪些**已注册的消费者**(规则/动作/沉淀技能)

分级:
  breaking  删除或改绑:下游必然失效
  risky     状态降级(verified→candidate)或别名移除:依赖强边的推理与锚定会退化
  safe      纯新增或状态升级:向后兼容

设计取舍:
- **下游影响是重点,不是附赠**。只报"删了 3 个对象"没有决策价值;
  报"删掉的 sales_order 上挂着 2 条审批规则和 1 个动作"才让人知道该不该删。
- **不阻断变更**。兼容性是决策依据,不是权限;强行拦截会让紧急修正也做不了。
  判断权在人,系统负责让人看清代价。
"""


def _rels(ir):
    if "links" in ir:
        return ir["links"], "source", "target"
    return ir.get("relations", []), "source_concept", "target_concept"


def _objs(ir):
    out = {}
    for i, o in enumerate(ir.get("objects", [])):
        k = o.get("id") or o.get("name") or f"_obj{i}"
        out[k] = o
    return out


def _rel_map(ir):
    rels, sk, tk = _rels(ir)
    m = {}
    for r in rels:
        s, t = r.get(sk), r.get(tk)
        if s and t:
            m[(s, t)] = r
    return m


STRONG = {"verified", "asserted"}


def diff(base_ir, new_ir):
    """结构层面的变更集。base=旧版本(基线),new=新版本。"""
    ob, on = _objs(base_ir), _objs(new_ir)
    rb, rn = _rel_map(base_ir), _rel_map(new_ir)

    removed_objs = [k for k in ob if k not in on]
    added_objs = [k for k in on if k not in ob]
    retable, realias, reattr = [], [], []
    for k in ob:
        if k not in on:
            continue
        a, b = ob[k], on[k]
        if (a.get("table") or "") != (b.get("table") or ""):
            retable.append({"object": k, "from": a.get("table"), "to": b.get("table")})
        la, lb = set(a.get("aliases") or []), set(b.get("aliases") or [])
        if la - lb:
            realias.append({"object": k, "removed": sorted(la - lb)})
        ca = {x.get("col") for x in (a.get("attrs") or []) if x.get("col")}
        cb = {x.get("col") for x in (b.get("attrs") or []) if x.get("col")}
        if ca - cb:
            reattr.append({"object": k, "removed": sorted(ca - cb)})

    removed_rels = [f"{s}->{t}" for (s, t) in rb if (s, t) not in rn]
    added_rels = [f"{s}->{t}" for (s, t) in rn if (s, t) not in rb]
    downgraded = []
    for pair, r in rb.items():
        if pair not in rn:
            continue
        sa = (r.get("status") or "")
        sb = (rn[pair].get("status") or "")
        if sa in STRONG and sb not in STRONG:
            downgraded.append({"relation": f"{pair[0]}->{pair[1]}", "from": sa, "to": sb})

    return {
        "removed_objects": removed_objs, "added_objects": added_objs,
        "retabled": retable, "removed_aliases": realias, "removed_attrs": reattr,
        "removed_relations": removed_rels, "added_relations": added_rels,
        "downgraded_relations": downgraded,
    }


def impact(changes, rules=None, actions=None, qa_skills=None, base_ir=None):
    """下游影响:变更命中了哪些已注册的消费者。

    这是本模块的重点——只报"删了 3 个对象"没有决策价值,
    报"删掉的对象上挂着 2 条规则和 1 个动作"才让人知道该不该删。
    """
    gone = set(changes.get("removed_objects") or [])
    retabled = {c["object"]: c for c in (changes.get("retabled") or [])}
    tables_gone = set()
    if base_ir:
        ob = _objs(base_ir)
        for k in gone:
            t = (ob.get(k, {}).get("table") or "").lower()
            if t:
                tables_gone.add(t)

    hits = []
    for r in (rules or []):
        on = r.get("on")
        if on in gone:
            hits.append({"kind": "rule", "id": r.get("id"), "cn": r.get("cn"),
                         "why": f"规则作用对象 {on} 被删除 —— 该规则将永不触发",
                         "severity": "breaking"})
        elif on in retabled:
            hits.append({"kind": "rule", "id": r.get("id"), "cn": r.get("cn"),
                         "why": f"规则作用对象 {on} 改绑表 "
                                f"{retabled[on]['from']}→{retabled[on]['to']}",
                         "severity": "risky"})
    for a in (actions or []):
        t = (a.get("object_table") or "").lower()
        if t and t in tables_gone:
            hits.append({"kind": "action", "id": a.get("id"), "cn": a.get("cn"),
                         "why": f"动作绑定表 {t} 所属对象被删除 —— 该动作将无法定位数据",
                         "severity": "breaking"})
    for s in (qa_skills or []):
        sql = " ".join(str(x.get("sql", "")) for x in (s.get("analyses") or []))
        low = sql.lower()
        for t in tables_gone:
            if t and t in low:
                hits.append({"kind": "qa_skill", "id": s.get("question", "")[:30],
                             "why": f"沉淀技能的 SQL 引用了被删对象的表 {t}",
                             "severity": "breaking"})
                break
    return hits


def check(base_ir, new_ir, rules=None, actions=None, qa_skills=None):
    """完整兼容性报告。"""
    ch = diff(base_ir, new_ir)
    hits = impact(ch, rules, actions, qa_skills, base_ir)

    breaking, risky = [], []
    for k in ch["removed_objects"]:
        breaking.append({"type": "object_removed", "target": k,
                         "desc": f"对象 {k} 被删除",
                         "fix": "引用它的查询/规则/动作会失效;确认无下游依赖再删"})
    for c in ch["removed_relations"]:
        breaking.append({"type": "relation_removed", "target": c,
                         "desc": f"关系 {c} 被删除",
                         "fix": "依赖该路径的穿透链路与 JOIN 提示会断"})
    for c in ch["retabled"]:
        breaking.append({"type": "object_retabled", "target": c["object"],
                         "desc": f"对象 {c['object']} 改绑表 {c['from']}→{c['to']}",
                         "fix": "已生成的 SQL 与动作绑定会指向旧表"})
    for c in ch["removed_attrs"]:
        breaking.append({"type": "attrs_removed", "target": c["object"],
                         "desc": f"对象 {c['object']} 移除属性 {', '.join(c['removed'][:5])}",
                         "fix": "引用这些列的口径与 SQL 会失效"})
    for c in ch["downgraded_relations"]:
        risky.append({"type": "status_downgraded", "target": c["relation"],
                      "desc": f"关系 {c['relation']} 状态降级 {c['from']}→{c['to']}",
                      "fix": "依赖强边的路径判定会退化为 partial;确认降级是否有据"})
    for c in ch["removed_aliases"]:
        risky.append({"type": "aliases_removed", "target": c["object"],
                      "desc": f"对象 {c['object']} 移除别名 {', '.join(c['removed'])}",
                      "fix": "对应业务用语将锚不到该对象,问数召回会退化"})

    verdict = "breaking" if breaking else ("risky" if risky else "safe")
    return {
        "verdict": verdict,
        "changes": ch,
        "breaking": breaking, "risky": risky,
        "downstream_impact": hits,
        "summary": {
            "added_objects": len(ch["added_objects"]),
            "removed_objects": len(ch["removed_objects"]),
            "added_relations": len(ch["added_relations"]),
            "removed_relations": len(ch["removed_relations"]),
            "breaking_count": len(breaking), "risky_count": len(risky),
            "downstream_hits": len(hits),
        },
        "note": "兼容性是决策依据,不是权限——本检查不阻断任何变更,"
                "只负责让人在改之前看清代价。新增与状态升级视为向后兼容",
    }
