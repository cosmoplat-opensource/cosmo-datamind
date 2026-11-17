#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工艺路线层与决策层(DR-117):工艺路线、约束、属性的数据来源(点亮矩阵)、审计。

图谱原先只有「表对应对象、键对应关系」。面向生产决策的本体还需要三样东西,它们都挂在已建模的对象上:

- **工艺路线(routes)**:按业务顺序排列的对象序列(工单 → 绕线 → 干燥 → 总装),可逐段标角色(流转 / 返工)。
  路线是声明;它成不成立由关系说了算——每次验收检查都会按方向核验(cq_check.check_route)。
- **约束(constraints)**:作用对象、类型、内容、参数、硬约束还是软约束、单位、目的、例外、依据。
  结构非法一律拒收;有可回放依据(代码位置、数据)的状态为 evidenced,否则为 candidate;certified 只能由人授予并署名。
- **点亮矩阵(lighting)**:每个对象的数据从哪里来(已接入 / 部分接入 / 推导 / 未接入),外加尚未接入的属性清单。
  扩展到新工厂、新领域时结构不变,先列出要接的属性再接数据。

所有函数只做确定性处理,不调模型,不依赖 Web 层。
"""
from __future__ import annotations

import re

KINDS = ("precedence", "capacity", "batching", "buffer", "calendar", "unit", "split", "material", "lock",
         "due_date", "horizon", "quality", "other")
EVIDENCE_TYPES = ("code", "sop", "interview", "document", "data")
STATUSES = ("candidate", "evidenced", "certified", "deprecated")
HARDNESS = ("hard", "soft")
LIGHT = ("lit", "partial", "derived", "unlit")
_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]{0,47}$")


def _object_names(ir):
    """约束、路线、点亮矩阵可以引用的对象指代:id、name、绑定的表。"""
    out = set()
    for o in ir.get("objects") or []:
        for k in ("id", "name", "table"):
            if o.get(k):
                out.add(str(o[k]))
    return out


def _evidence(items):
    ev = []
    for e in items or []:
        if not isinstance(e, dict) or not str(e.get("source") or "").strip():
            raise ValueError("evidence 每项须为含 source 的对象")
        typ = e.get("type") if e.get("type") in EVIDENCE_TYPES else ("code" if e.get("lines") else "document")
        ev.append({"type": typ, "source": str(e["source"])[:120], "lines": str(e.get("lines") or "")[:40],
                   "what": str(e.get("what") or "")[:200]})
    return ev[:10]


def _status(given, evidence, certified_by):
    """依据决定状态:人确认的保持 certified(须署名),停用的保持 deprecated;其余有可回放依据为 evidenced。"""
    if given == "certified":
        if not certified_by:
            raise ValueError("certified 须记录确认人(certified_by)")
        return "certified"
    if given == "deprecated":
        return "deprecated"
    replayable = any((e["type"] == "code" and e["lines"]) or e["type"] == "data" for e in evidence)
    return "evidenced" if replayable else "candidate"


# ── 约束 ────────────────────────────────────────────────────────────────
def normalize_constraint(c, ir):
    """归一并校验一条约束;非法抛 ValueError。"""
    if not isinstance(c, dict):
        raise ValueError("约束须为对象")
    cid = str(c.get("id") or "").strip()
    if not _ID.match(cid):
        raise ValueError("id 须以字母开头,1–48 位字母数字下划线连字符")
    cn = str(c.get("cn") or "").strip()[:60]
    if not cn:
        raise ValueError("cn(约束中文名)必填")
    on = str(c.get("on") or "").strip()
    if on not in _object_names(ir):
        raise ValueError(f"作用对象 {on} 不在本体中:约束须锚定到已建模的对象")
    kind = c.get("kind") if c.get("kind") in KINDS else None
    if not kind:
        raise ValueError(f"kind 须为 {'/'.join(KINDS)}")
    rule = str(c.get("rule") or "").strip()[:500]
    if not rule:
        raise ValueError("rule(约束内容)必填")
    hardness = c.get("hardness") or "hard"
    if hardness not in HARDNESS:
        raise ValueError("hardness 须为 hard(不可违反)或 soft(可权衡)")
    ev = _evidence(c.get("evidence"))
    out = {"id": cid, "cn": cn, "on": on, "kind": kind, "rule": rule, "hardness": hardness,
           "params": c.get("params") if isinstance(c.get("params"), dict) else {},
           "evidence": ev, "status": _status(c.get("status"), ev, c.get("certified_by"))}
    # 目的、例外、单位、生效范围:由工艺或计划负责人确认的业务含义,与数据绑定分开记
    for k, cap in (("unit", 24), ("purpose", 200), ("exceptions", 300), ("applies_to", 80),
                   ("certified_by", 40), ("certified_at", 40)):
        if c.get(k):
            out[k] = str(c[k])[:cap]
    return out


# ── 工艺路线 ────────────────────────────────────────────────────────────
def normalize_route(r, ir):
    """归一并校验一条工艺路线;非法抛 ValueError。steps 是按业务顺序排列的对象名,roles 逐段给角色。"""
    if not isinstance(r, dict):
        raise ValueError("路线须为对象")
    rid = str(r.get("id") or "").strip()
    if not _ID.match(rid):
        raise ValueError("id 须以字母开头,1–48 位字母数字下划线连字符")
    cn = str(r.get("cn") or "").strip()[:60]
    if not cn:
        raise ValueError("cn(路线中文名)必填")
    steps = r.get("steps")
    if not isinstance(steps, list) or not 2 <= len(steps) <= 40 or not all(isinstance(s, str) and s.strip() for s in steps):
        raise ValueError("steps 须为 2–40 个对象名的列表,按业务顺序排列")
    steps = [s.strip() for s in steps]
    known = _object_names(ir)
    unknown = [s for s in steps if s not in known]
    if unknown:
        raise ValueError("路线里的对象不在本体中: " + "、".join(unknown))
    roles = r.get("roles") or []
    if not isinstance(roles, list) or len(roles) > len(steps) - 1:
        raise ValueError("roles 逐段给角色,至多比 steps 少一项")
    roles = [str(x or "").strip()[:24] for x in roles]
    ev = _evidence(r.get("evidence"))
    out = {"id": rid, "cn": cn, "steps": steps, "roles": roles, "evidence": ev,
           "status": _status(r.get("status"), ev, r.get("certified_by"))}
    for k, cap in (("version", 40), ("applies_to", 80), ("certified_by", 40), ("certified_at", 40)):
        if r.get(k):
            out[k] = str(r[k])[:cap]
    return out


def route_cqs(ir):
    """图谱上声明的每条在用路线对应一条按方向核验的验收问题(停用的路线不参与)。"""
    out = []
    for r in ir.get("routes") or []:
        if isinstance(r, dict) and r.get("status") != "deprecated" and isinstance(r.get("steps"), list) and len(r["steps"]) >= 2:
            out.append({"q": f"工艺路线「{r.get('cn') or r.get('id')}」的各道工序能否按顺序走通",
                        "route": list(r["steps"]), "roles": list(r.get("roles") or []), "route_id": r.get("id")})
    return out


def main_chain_from_route(ir, route):
    """把一条工艺路线(概念层)换成追踪用的主线声明(数据层),这样路线只需声明一次(DR-121)。

    相邻两步之间要有已验证或人工确认、且带单列连接键的关系,方向不限:
    下一步是子表时按它的外键往下查;下一步是父表时按父键查。走不下去就停在那里,已得到的部分照常可用。
    根对象的标识列取对象声明的主键,否则取它在已验证关系里作为父表时的父键;图谱里没有时留空,由取数一侧读表的主键。
    """
    objs: dict = {}
    for o in ir.get("objects") or []:
        for k in ("id", "name", "table"):
            if o.get(k):
                objs.setdefault(str(o[k]), o)
    names = [str(s) for s in (route or {}).get("steps") or []]
    if len(names) < 2 or any(n not in objs or not objs[n].get("table") for n in names):
        return {}
    steps: list[dict] = [objs[n] for n in names]

    roles = [str(x or "").strip() for x in (route or {}).get("roles") or []]

    def link(a, b, role):
        """a、b 之间用于取数的关系 → (b 表上的列, a 表上的列, 角色, 方向);没有返回 None;
        路线没指明角色而两步之间有不止一条可用关系时返回 "ambiguous":不替使用者挑一条。
        与按方向核验路线时用的是同一条关系:路线指定了角色,就只认那个角色(DR-126)。"""
        an, bn = a.get("name"), b.get("name")
        found = []
        for r in ir.get("relations") or []:
            ev = r.get("evidence") or {}
            ck, pk = str(ev.get("child_key") or ""), str(ev.get("parent_key") or "")
            if r.get("status") not in ("verified", "asserted") or not ck or not pk or "," in ck or "," in pk:
                continue
            rel_role = str(r.get("role") or "")
            if role and rel_role != role:
                continue
            if (r.get("source_concept"), r.get("target_concept")) == (bn, an):       # b 是子表
                found.append((ck, pk, rel_role, "child"))
            elif (r.get("source_concept"), r.get("target_concept")) == (an, bn):     # b 是父表
                found.append((pk, ck, rel_role, "parent"))
        if len(found) > 1:
            return "ambiguous"
        return found[0] if found else None

    def stop(chain, a, b, why):
        chain["stopped"] = {"after": a.get("cn") or a.get("name"), "before": b.get("cn") or b.get("name"), "reason": why}

    first = link(steps[0], steps[1], roles[0] if roles else "")
    # 根对象的标识列:对象声明的主键;否则别的已验证关系里它作为父表时的父键;否则第一段关系里它是父表时的父键。
    # 根对象在第一段里是子表时,first[1] 是它的外键列,不能当标识列:留空,由取数一侧读表的主键。
    as_parent = {str((r.get("evidence") or {}).get("parent_key") or "") for r in ir.get("relations") or []
                 if r.get("target_concept") == steps[0].get("name") and r.get("status") in ("verified", "asserted")}
    as_parent = {k for k in as_parent if k and "," not in k}
    root_key = steps[0].get("pk") or (next(iter(as_parent)) if len(as_parent) == 1 else None) \
        or (first[1] if isinstance(first, tuple) and first[3] == "child" else None) or ""
    if "," in str(root_key):
        return {}
    hops: list[dict] = []
    chain: dict = {"root": {"table": steps[0]["table"], "key": str(root_key), "label": steps[0].get("cn") or steps[0].get("name")},
                   "hops": hops, "derived_from_route": (route or {}).get("id")}
    for i, (a, b) in enumerate(zip(steps, steps[1:], strict=False)):
        role = roles[i] if i < len(roles) else ""
        cols = link(a, b, role)
        if cols == "ambiguous":
            stop(chain, a, b, "这两步之间有不止一条可用的关系,请在路线里指明角色")
            break
        if not cols:
            stop(chain, a, b, f"没有已验证的「{role}」关系" if role else "没有已验证的关系")
            break
        hop = {"table": b["table"], "fk": cols[0], "parent": a["table"], "pk": cols[1],
               "label": b.get("cn") or b.get("name"), "direction": cols[3]}
        if cols[2]:
            hop["role"] = cols[2]
        if b.get("pk"):
            hop["key"] = b["pk"]
        hops.append(hop)
    return chain if hops else {}


def main_chain(ir):
    """追踪用的主线:图谱显式声明的优先;没有时从第一条在用且能走通的工艺路线推出。都没有返回空字典。"""
    declared = (ir or {}).get("main_chain")
    if isinstance(declared, dict) and declared.get("root"):
        return declared
    for r in (ir or {}).get("routes") or []:
        if isinstance(r, dict) and r.get("status") != "deprecated":
            chain = main_chain_from_route(ir, r)
            if chain:
                return chain
    return {}


# ── 点亮矩阵 ────────────────────────────────────────────────────────────
def normalize_lighting(entry):
    if not isinstance(entry, dict) or entry.get("status") not in LIGHT:
        raise ValueError(f"status 须为 {'/'.join(LIGHT)}")
    return {"status": entry["status"], "source": str(entry.get("source") or "")[:160],
            "note": str(entry.get("note") or "")[:200]}


# ── 审计 ────────────────────────────────────────────────────────────────
def audit(ir, rules=()):
    """决策层的完整度:约束有没有依据、有没有对应的规则,路线有没有依据,对象的数据来源是否都交代了。"""
    cons = [c for c in ir.get("constraints") or [] if isinstance(c, dict)]
    routes = [r for r in ir.get("routes") or [] if isinstance(r, dict)]
    lighting = (ir.get("lighting") or {}).get("objects") or {}
    objs = [o for o in ir.get("objects") or [] if o.get("table")]
    rule_notes = {str(r.get("note") or "") for r in rules}
    by_status = {s: sum(1 for o in objs if (lighting.get(o.get("name")) or {}).get("status") == s) for s in LIGHT}
    backed = ("evidenced", "certified")
    return {
        "constraints": len(cons),
        "constraints_evidenced": sum(1 for c in cons if c.get("status") in backed),
        "constraints_certified": sum(1 for c in cons if c.get("status") == "certified"),
        "constraints_hard": sum(1 for c in cons if c.get("hardness") == "hard"),
        "constraints_without_rule": [c["id"] for c in cons if c.get("id") not in rule_notes],
        "routes": len(routes),
        "routes_evidenced": sum(1 for r in routes if r.get("status") in backed),
        "objects": len(objs),
        "lighting": by_status,
        "objects_without_lighting": [o.get("name") for o in objs if o.get("name") not in lighting],
        "unlit_attributes": len((ir.get("lighting") or {}).get("unlit") or []),
    }


def review_items(ir):
    """决策层里还需要人处理的事项,供验收检查并入待评审清单。"""
    out = []
    cons = [c for c in ir.get("constraints") or [] if isinstance(c, dict) and c.get("status") == "candidate"]
    if cons:
        out.append({"type": "constraints_without_evidence", "count": len(cons),
                    "desc": f"{len(cons)} 条约束还没有可回放的依据(代码位置或数据),也未经负责人确认",
                    "fix": "补依据,或由工艺、计划负责人确认规则的目的、例外、单位与生效范围后置为 certified"})
    routes = [r for r in ir.get("routes") or [] if isinstance(r, dict) and r.get("status") == "candidate"]
    if routes:
        out.append({"type": "routes_without_evidence", "count": len(routes),
                    "desc": f"{len(routes)} 条工艺路线还没有依据,也未经负责人确认",
                    "fix": "补工艺文件或数据依据,或由工艺负责人确认后置为 certified"})
    return out
