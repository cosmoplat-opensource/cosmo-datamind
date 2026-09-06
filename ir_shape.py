#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""IR 形状兼容规则的单一事实源。

系统里并存两种 IR 形状:示例图谱用 ``links``(端点键 source/target),构建产物用
``relations``(端点键 source_concept/target_concept)。这条兼容规则此前在
server / cq_check / drift_check / compat_check / module_split / health_check
六处各抄一份——形状一旦扩展(如第三种端点键),就要改六个地方且极易漏改。
对象可指代名称的取名规则同理,cq_check 与 intent_check 各有一份且**不一致**:
intent_check 少收了 app 形状的 ``tables``,多表概念在意图核验里无法按表名指代。

本模块只依赖 stdlib,供全部确定性检查模块与 server 共同 import。
"""
from __future__ import annotations


def rels(ir, create=False):
    """关系列表 + 端点键名,兼容两种 IR 形状。

    ``create=True`` 时对构建产物形状用 setdefault(server 的编辑回放需要在
    缺 relations 键的旧产物上就地补空列表);检查类调用保持只读,不改动入参。
    """
    if "links" in ir:
        return ir["links"], "source", "target"
    if create:
        return ir.setdefault("relations", []), "source_concept", "target_concept"
    return ir.get("relations", []), "source_concept", "target_concept"


# 中文表名后缀:业务人员说「销售订单」,本体里叫「销售订单事实表」。锚定/召回时都要
# 按剥离后的名字匹配,否则最常见的中文命名方式一条都召不回。此前 server 的指代延续与
# 根因诊断各抄一份,现收敛到此处(cq_check 的验收判定刻意保持严格,不用这组变体)。
CN_TABLE_SUFFIXES = ("事实表", "维度表", "汇总表", "明细表", "表")


def strip_cn_suffix(name):
    """剥离一个已知中文表名后缀;剥完少于两字则不剥(「单表」剥成「单」会乱命中)。"""
    n = (name or "").strip()
    for suf in CN_TABLE_SUFFIXES:
        if n.endswith(suf) and len(n) - len(suf) >= 2:
            return n[: -len(suf)]
    return n


def name_variants(o):
    """对象的可指代名称及其剥离后缀后的形式(去重保序)。"""
    out = []
    for n in obj_names(o):
        for v in (n, strip_cn_suffix(n)):
            if v and v not in out:
                out.append(v)
    return out


def obj_names(o):
    """一个对象的全部可指代名称——把自然语言词锚定到对象时用。

    app 形状的对象没有 id、可绑多张表(``tables``);demo 形状则 id/name/cn/table
    都可能被业务人员用来指代;DR-027 的业务别名(``aliases``)两种形状都有。
    统一取超集:少收一个键的后果是「明明存在的对象说找不到」,比多收严重得多。
    """
    out = []
    for k in ("id", "name", "cn", "table"):
        v = o.get(k)
        if isinstance(v, str) and v.strip():
            out.append(v.strip())
    for a in (o.get("aliases") or []):
        if isinstance(a, str) and a.strip():
            out.append(a.strip())
    for t in (o.get("tables") or []):
        if isinstance(t, str) and t.strip():
            out.append(t.strip())
    return out
