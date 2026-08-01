#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""能力问题(Competency Questions)核验 —— DR-024。

在**已建成的本体 IR** 上做确定性的结构可达性判定,回答一个记分卡回答不了的问题:
「这个本体到底能不能支撑当初要解决的业务问题」。

判定完全不调 LLM:对象在不在、路径通不通、边的状态够不够,都是图上算出来的,
可回放、可解释。这与本系统的反造假纪律同源——让模型自评"能不能答",
只会把"看起来能答"当成"能答"。

结论三态:
  answerable    涉及的对象全部锚定,且两两之间存在仅经 verified/asserted 的路径
  partial       对象锚定了,但连通路径必须借道 candidate/gap 边(骨架对、证据不足)
  unanswerable  有对象锚不到,或对象间根本不连通(建模漏了概念)

边界(必须如实标注,不可省略):
  本模块只判**结构**,不执行查询。它能证伪("路径不存在,必然答不了"),
  不能证成("路径存在"不等于数据里真有值)。数据侧验证由 /api/eval/* 承担。
"""
import re
from collections import deque

# 证据充分的边:人审断言与数据裁决均可采信(与 DR-013 的证据分层一致)
STRONG = {"verified", "asserted"}


def _rels(ir):
    """兼容两种 IR 形状:demo 的 links[source/target] 与构建产物的 relations[source_concept/target_concept]"""
    if "links" in ir:
        return ir["links"], "source", "target"
    return ir.get("relations", []), "source_concept", "target_concept"


def _obj_names(o):
    """一个对象的全部可指代名称——用于把 CQ 里的自然语言词锚定到对象。
    app 形状的对象没有 id,只有 name;demo 形状则 id/name/cn/table 都可能被业务人员用来指代。"""
    out = []
    for k in ("id", "name", "cn", "table"):
        v = o.get(k)
        if isinstance(v, str) and v.strip():
            out.append(v.strip())
    for t in (o.get("tables") or []):          # app 形状:一个概念可绑多张表
        if isinstance(t, str) and t.strip():
            out.append(t.strip())
    return out


def _key_of(o, idx):
    """对象在关系端点中使用的键:demo 用 id,app 用 name;都缺则退化为序号占位"""
    return o.get("id") or o.get("name") or o.get("cn") or f"_obj{idx}"


def anchor_objects(question, ir):
    """把 CQ 文本锚定到本体对象。

    从严匹配:只认**完整出现**在问句里的名称,不做模糊/子串近似——
    近似匹配会把"单"匹配到"工单",制造虚假的 answerable。
    宁可漏匹配(结果偏保守,报出缺口),不可错匹配(结果偏乐观,掩盖缺口)。
    """
    q = (question or "").lower()
    hits, seen = [], set()
    for i, o in enumerate(ir.get("objects", [])):
        key = _key_of(o, i)
        if key in seen:
            continue
        for nm in _obj_names(o):
            n = nm.lower()
            # 中文名 ≥2 字、英文标识 ≥3 字符才参与匹配,避免单字/短码误命中
            if (len(n) >= 2 if re.search(r"[一-鿿]", n) else len(n) >= 3) and n in q:
                hits.append({"key": key, "matched": nm,
                             "cn": o.get("cn") or o.get("name") or key})
                seen.add(key)
                break
    return hits


def _adj(ir, strong_only):
    """无向邻接表。strong_only=True 时只保留证据充分的边。"""
    rels, sk, tk = _rels(ir)
    g = {}
    for r in rels:
        if strong_only and (r.get("status") or "") not in STRONG:
            continue
        s, t = r.get(sk), r.get(tk)
        if not s or not t:
            continue
        g.setdefault(s, set()).add(t)
        g.setdefault(t, set()).add(s)
    return g


def _path(g, a, b):
    """BFS 最短路;不通返回 None。同一对象视为长度 0 的平凡路径。"""
    if a == b:
        return [a]
    if a not in g or b not in g:
        return None
    prev, dq = {a: None}, deque([a])
    while dq:
        cur = dq.popleft()
        for nx in g.get(cur, ()):
            if nx in prev:
                continue
            prev[nx] = cur
            if nx == b:
                out = [nx]
                while prev[out[-1]] is not None:
                    out.append(prev[out[-1]])
                return list(reversed(out))
            dq.append(nx)
    return None


def check_one(question, ir, expect=None):
    """核验单条 CQ。expect 为可选的期望对象名列表(业务方显式声明该问题应涉及哪些对象)。"""
    anchors = anchor_objects(question, ir)
    keys = [a["key"] for a in anchors]

    missing_expected = []
    if expect:
        known = {n.lower() for o in ir.get("objects", []) for n in _obj_names(o)}
        missing_expected = [e for e in expect if e.lower() not in known]

    if missing_expected:
        return {"question": question, "verdict": "unanswerable",
                "anchors": anchors, "path": None,
                "reason": "本体中不存在声明的对象: " + "、".join(missing_expected),
                "fix": "补建模:先让抽取环节产出这些对象,再重新核验"}
    if not anchors:
        return {"question": question, "verdict": "unanswerable",
                "anchors": [], "path": None,
                "reason": "问句未能锚定到任何本体对象(名称未在本体中出现)",
                "fix": "补建模,或为对象补中文别名使业务用语可被锚定"}
    if len(keys) == 1:
        return {"question": question, "verdict": "answerable",
                "anchors": anchors, "path": [keys[0]],
                "reason": "单对象问题,无需跨对象路径",
                "fix": ""}

    g_strong, g_all = _adj(ir, True), _adj(ir, False)
    weak_pairs, broken_pairs, paths = [], [], []
    for i in range(len(keys) - 1):           # 相邻配对连通即可支撑链式追问
        a, b = keys[i], keys[i + 1]
        p = _path(g_strong, a, b)
        if p:
            paths.append(p)
            continue
        p2 = _path(g_all, a, b)
        if p2:
            weak_pairs.append((a, b))
            paths.append(p2)
        else:
            broken_pairs.append((a, b))

    if broken_pairs:
        return {"question": question, "verdict": "unanswerable",
                "anchors": anchors, "path": paths,
                "reason": "对象间不连通: " + "、".join(f"{a}↔{b}" for a, b in broken_pairs),
                "fix": "补关系:这两个对象之间缺少任何路径,需在抽取或人审阶段补建"}
    if weak_pairs:
        return {"question": question, "verdict": "partial",
                "anchors": anchors, "path": paths,
                "reason": "连通路径须借道 candidate/gap 边: " + "、".join(f"{a}↔{b}" for a, b in weak_pairs),
                "fix": "补证据:对沿途候选关系做数据裁决或人审,升级为 verified/asserted"}
    return {"question": question, "verdict": "answerable",
            "anchors": anchors, "path": paths,
            "reason": "全部对象已锚定,且路径仅经 verified/asserted 边",
            "fix": ""}


def check_all(cqs, ir):
    """批量核验,返回覆盖报告。cqs 元素可为 str,或 {"q": ..., "expect": [...]}。"""
    items = []
    for c in (cqs or []):
        if isinstance(c, str):
            items.append(check_one(c, ir))
        elif isinstance(c, dict) and (c.get("q") or c.get("question")):
            items.append(check_one(c.get("q") or c.get("question"), ir, c.get("expect")))
    n = len(items)
    cnt = {v: sum(1 for i in items if i["verdict"] == v)
           for v in ("answerable", "partial", "unanswerable")}
    return {
        "total": n,
        "counts": cnt,
        # 覆盖率只计 answerable:partial 意味着证据不足,不能算"能答"
        "coverage": round(cnt["answerable"] * 100.0 / n, 1) if n else 0.0,
        "items": items,
        "note": "结构可达性判定,不执行查询;answerable 表示本体结构支持该问题,"
                "不代表数据中一定有值——数据侧验证见问数评测",
    }


def gaps_from(report):
    """把不可答/部分可答的 CQ 转成缺口条目,回流进 IR 的 gaps 清单。"""
    out = []
    for it in report.get("items", []):
        if it["verdict"] == "answerable":
            continue
        out.append({
            "type": "cq_unanswerable" if it["verdict"] == "unanswerable" else "cq_partial",
            "desc": f"能力问题未被支撑:{it['question']} —— {it['reason']}",
            "fix": it["fix"],
        })
    return out
