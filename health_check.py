#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本体健康度体检 —— DR-030。

《本体智能研究报告(1.0)》阶段六(持续维护与演化)要求:

> 知识质量的长效监控(如**异常关系检测**、三元组冲突检测)。
> 建议组建专门团队……定期评审本体的**健康度**与适应度。

已有的三项检测各管一面,但都不看**图结构本身**:
  CQ 核验    —— 本体够不够用(能不能答业务问题)
  漂移检测   —— 本体还对不对得上数据源
  完备度卡   —— 定义/反例/接地填没填全

本模块补上「图结构层面的异常」:一个定义 100%、与数据源完全一致、
CQ 全过的本体,结构上仍可能是病的——比如一半对象是孤岛(建了但连不上),
或存在自反关系(A 关联 A),或同一对语义重复连了三条边。
这些不会让任何现有检查报错,却会让问数召回时选错表、让穿透链路断在意外处。

检出项(全部确定性图计算,不调 LLM):
  isolated        孤岛对象:不参与任何关系 —— 建了却用不上
  self_loop       自反关系:源=目标 —— 多为抽取误判
  duplicate       重复边:同一对象对之间多条同向关系 —— 口径二义
  bidirectional   双向对偶:A→B 与 B→A 同时存在 —— 方向未定,推理会绕圈
  hub             超级节点:度数远超均值 —— 常是"万能表"未拆分
  dangling        悬空端点:关系引用了不存在的对象 —— IR 自身不一致
  status_conflict 状态矛盾:同一对关系既 verified 又 rejected

设计取舍:
- **只诊断,不自动修**。孤岛可能是刚建还没连,超级节点可能就是事实上的枢纽
  (如"订单"天然连接一切)。自动删会毁掉建模成果;判断权在人。
- **分级而非一刀切**。dangling/status_conflict 是硬错误(IR 不自洽),
  isolated/hub 是待核查信号——混为一谈会让人淹没在噪声里而忽略真问题。
- **健康分只由硬错误扣分**。信号类不扣分,只列出;否则一个枢纽对象就能
  把分数拉垮,分数失去意义。
"""
from collections import Counter


def _rels(ir):
    if "links" in ir:
        return ir["links"], "source", "target"
    return ir.get("relations", []), "source_concept", "target_concept"


def _key(o, i):
    return o.get("id") or o.get("name") or f"_obj{i}"


def check(ir):
    objs = ir.get("objects", [])
    rels, sk, tk = _rels(ir)
    keys = {_key(o, i) for i, o in enumerate(objs)}
    cn = {_key(o, i): (o.get("cn") or o.get("name") or _key(o, i)) for i, o in enumerate(objs)}

    errors, signals = [], []
    deg = Counter()
    pair_dir, pair_undir = Counter(), {}

    for r in rels:
        s, t = r.get(sk), r.get(tk)
        st = (r.get("status") or "")
        if not s or not t:
            errors.append({"type": "dangling", "severity": "error",
                           "desc": f"关系端点缺失: {s or '(空)'} → {t or '(空)'}",
                           "fix": "IR 自身不一致:补全端点或移除该关系"})
            continue
        for side in (s, t):
            if side not in keys:
                errors.append({"type": "dangling", "severity": "error",
                               "relation": f"{s}->{t}",
                               "desc": f"关系引用了不存在的对象: {side}",
                               "fix": "对象已被删除但关系残留,须级联清理"})
        if s == t:
            errors.append({"type": "self_loop", "severity": "error",
                           "relation": f"{s}->{t}", "verb": r.get("verb"),
                           "desc": f"自反关系:「{cn.get(s, s)}」指向自己",
                           "fix": "多为抽取误判(同名列自连);确认后删除或改为层次关系"})
        deg[s] += 1
        deg[t] += 1
        pair_dir[(s, t)] += 1
        pair_undir.setdefault(frozenset((s, t)), []).append((s, t, st, r.get("verb")))

    for (s, t), c in pair_dir.items():
        if c > 1 and s != t:
            signals.append({"type": "duplicate", "severity": "warn",
                            "relation": f"{s}->{t}", "count": c,
                            "desc": f"「{cn.get(s, s)}」→「{cn.get(t, t)}」有 {c} 条同向关系",
                            "fix": "口径二义:合并为一条,或用不同动词明确区分语义"})
    for pair, items in pair_undir.items():
        dirs = {(a, b) for a, b, _, _ in items}
        if len(dirs) > 1:
            a, b = tuple(pair)[0], tuple(pair)[-1]
            signals.append({"type": "bidirectional", "severity": "warn",
                            "relation": f"{a}<->{b}",
                            "desc": f"「{cn.get(a, a)}」与「{cn.get(b, b)}」互指(双向对偶)",
                            "fix": "方向未定会让沿关系推理绕圈;确定主方向后删除反向边"})
        sts = {st for _, _, st, _ in items}
        if "verified" in sts and "rejected" in sts:
            a, b = tuple(pair)[0], tuple(pair)[-1]
            errors.append({"type": "status_conflict", "severity": "error",
                           "relation": f"{a}<->{b}",
                           "desc": f"同一对象对间既有 verified 又有 rejected 关系",
                           "fix": "证据自相矛盾:须人审裁定保留哪条"})

    isolated = [k for k in keys if deg[k] == 0]
    for k in isolated:
        signals.append({"type": "isolated", "severity": "info", "object": k,
                        "desc": f"孤岛对象:「{cn.get(k, k)}」不参与任何关系",
                        "fix": "建了却连不上——补关系,或确认它本就是独立参考数据"})

    if deg:
        avg = sum(deg.values()) / len(deg)
        for k, d in deg.most_common(5):
            if avg > 0 and d >= max(6, avg * 4):
                signals.append({"type": "hub", "severity": "info", "object": k, "degree": d,
                                "desc": f"超级节点:「{cn.get(k, k)}」度数 {d}(均值 {avg:.1f})",
                                "fix": "常见于未拆分的『万能表』;若确为业务枢纽则属正常"})

    n_checked = len(objs) + len(rels)
    score = round(max(0.0, (n_checked - len(errors) * 2) * 100.0 / n_checked), 1) if n_checked else 100.0
    return {
        "objects": len(objs), "relations": len(rels),
        "isolated_count": len(isolated),
        "errors": errors, "signals": signals,
        "error_count": len(errors), "signal_count": len(signals),
        "healthy": not errors,
        "score": score,
        "note": "确定性图结构体检,不调 LLM;只诊断不自动修——孤岛可能是刚建未连,"
                "超级节点可能本就是业务枢纽,判断权在人。健康分只由硬错误扣分,"
                "信号类只列出不扣分(否则一个枢纽对象就能把分数拉垮)",
    }


def gaps_from(report):
    """硬错误转缺口条目回流;信号类不进缺口(避免噪声淹没真问题)。"""
    return [{"type": "health_" + e["type"], "desc": e["desc"], "fix": e["fix"]}
            for e in report.get("errors", [])]
