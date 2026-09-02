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
