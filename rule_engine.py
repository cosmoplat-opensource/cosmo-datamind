#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""业务规则与约束 + 确定性推理引擎 —— DR-028。

《本体智能研究报告(1.0)》的三层架构里,我们此前只做了语义层的前三要素与行动层:

  语义层:对象 ✓ 属性 ✓ 关系 ✓ **规则与约束 ✗**
  决策层:逻辑推理 / 一致性校验 / 触发条件 / 业务判定  —— **整层缺失**
  行动层:API / 工作流 / 动作编排 / 权限控制 ✓(动作中心)

报告对决策层的要求很具体:"逻辑推理基于语义层的概念关系与业务规则,推导出未显式
记录的**隐含结论**,如由审批层级与金额规则推导出某笔订单应走的审批路径";
"形成完整可追溯的**决策路径**——每一条结论均可回溯至具体规则依据,支持合规审计"。

本模块补齐这一层,设计遵循本系统一贯规范:

- **推理是确定性的,不调 LLM**。规则是业务写死的逻辑边界,用模型"推"会把
  概率当逻辑;报告要的"每条结论可回溯至具体规则依据",模型给不了这种回溯。
- **每条结论必带 trace**。结论不能只给答案,要给"依据哪条规则、哪个字段、
  什么阈值"——这是合规审计的最低要求,也是与"猜一个"的分界线。
- **规则冲突要报出来,不能静默取一条**。两条规则对同一情形给出相反判定时,
  系统无权替业务做选择;静默取第一条等于把冲突藏进黑箱。

规则形态(刻意保持朴素,不引入规则 DSL):
  {
    "id": "r_high_amount",
    "cn": "大额订单需总监审批",
    "on": "sales_order",                    # 作用对象(本体对象键)
    "when": [{"field": "amount", "op": ">=", "value": 100000}],   # 全部满足才触发
    "then": {"decision": "需总监审批", "action": "escalate_approval", "severity": "high"},
    "note": "依据 2026 版审批权限手册"
  }
"""
import re

_OPS = {
    ">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b, "<": lambda a, b: a < b,
    "==": lambda a, b: a == b, "!=": lambda a, b: a != b,
    "in": lambda a, b: a in b if isinstance(b, (list, tuple, str)) else False,
    "contains": lambda a, b: str(b) in str(a),
    "exists": lambda a, b: a is not None and a != "",
    "missing": lambda a, b: a is None or a == "",
}

_ALLOWED_SEVERITY = ("low", "medium", "high")


def validate_rule(r):
    """规则校验:结构非法一律拒收——规则是逻辑边界,带病入库会污染全部下游判定。"""
    if not isinstance(r, dict):
        return "规则须为对象"
    rid = str(r.get("id") or "").strip()
    if not re.match(r"^[A-Za-z0-9_\-]{1,40}$", rid):
        return "id 需为 1-40 位字母数字下划线连字符"
    if not str(r.get("cn") or "").strip():
        return "cn(规则中文名)必填 —— 规则须可被业务读懂"
    if not str(r.get("on") or "").strip():
        return "on(作用对象)必填"
    conds = r.get("when")
    if not isinstance(conds, list) or not conds:
        return "when 需为非空条件列表"
    for c in conds:
        if not isinstance(c, dict) or not c.get("field"):
            return "条件需含 field"
        if c.get("op") not in _OPS:
            return f"不支持的运算符: {c.get('op')}(可用: {', '.join(_OPS)})"
    then = r.get("then")
    if not isinstance(then, dict) or not str(then.get("decision") or "").strip():
        return "then.decision(结论)必填"
    if then.get("severity") and then["severity"] not in _ALLOWED_SEVERITY:
        return f"severity 需为 {_ALLOWED_SEVERITY} 之一"
    return None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _eval_cond(c, facts):
    """单条件求值。返回 (是否满足, 可读的求值轨迹)。"""
    f = c["field"]
    op = c["op"]
    want = c.get("value")
    got = facts.get(f)
    a, b = _num(got), _num(want)
    if a is not None and b is not None and op in (">=", "<=", ">", "<", "==", "!="):
        got_c, want_c = a, b                      # 数值可比时按数值比,避免 "100" < "99" 的字符串陷阱
    else:
        got_c, want_c = got, want
    try:
        hit = bool(_OPS[op](got_c, want_c))
    except Exception:
        hit = False
    return hit, {"field": f, "op": op, "expect": want, "actual": got, "hit": hit}


def evaluate(rules, obj_key, facts):
    """对一个对象实例求值全部规则。

    facts 为该实例的字段字典(如 {"amount": 250000, "region": "华东"})。
    返回触发的结论、逐条求值轨迹、以及冲突提示。
    """
    fired, skipped, traces = [], [], []
    for r in rules:
        if str(r.get("on")) != str(obj_key):
            continue
        conds = []
        all_hit = True
        for c in r.get("when", []):
            hit, tr = _eval_cond(c, facts)
            conds.append(tr)
            if not hit:
                all_hit = False
        item = {"rule": r.get("id"), "cn": r.get("cn"), "conditions": conds,
                "note": r.get("note", "")}
        traces.append({**item, "fired": all_hit})
        if all_hit:
            then = r.get("then") or {}
            fired.append({
                "rule": r.get("id"), "cn": r.get("cn"),
                "decision": then.get("decision"),
                "action": then.get("action"),
                "severity": then.get("severity") or "medium",
                # 决策路径:结论可回溯到"哪条规则、哪个字段、什么阈值、实际值多少"
                "trace": conds, "basis": r.get("note", ""),
            })
        else:
            skipped.append(r.get("id"))

    # 冲突检测:同一 action 被赋予不同 decision,或同一 severity 层级给出相反结论
    conflicts = []
    by_action = {}
    for f in fired:
        if f.get("action"):
            by_action.setdefault(f["action"], []).append(f)
    for act, group in by_action.items():
        decisions = {g["decision"] for g in group}
        if len(decisions) > 1:
            conflicts.append({
                "action": act, "rules": [g["rule"] for g in group],
                "decisions": sorted(decisions),
                "why": "多条规则对同一动作给出不同结论,系统不替业务择一 —— 须人工裁定或修订规则",
            })
    return {
        "object": obj_key, "facts": facts,
        "fired": fired, "fired_count": len(fired),
        "skipped_count": len(skipped),
        "conflicts": conflicts,
        "traces": traces,
        "note": "确定性规则求值,不调 LLM;每条结论均带 trace 可回溯至具体规则与字段阈值。"
                "未触发不等于合规——只说明现有规则未覆盖该情形",
    }


def consistency_check(rules):
    """规则集一致性校验(报告决策层要素之一):在**不看数据**的前提下静态查问题。"""
    issues = []
    seen = {}
    for r in rules:
        rid = r.get("id")
        if rid in seen:
            issues.append({"type": "duplicate_id", "rule": rid,
                           "desc": f"规则 id 重复: {rid}",
                           "fix": "规则 id 须唯一,否则回溯时无法定位依据"})
        seen[rid] = r
    # 同对象同字段的阈值区间是否存在矛盾(如 >=100 判 A、>=100 判 B)
    sig = {}
    for r in rules:
        key = (r.get("on"), tuple(sorted(
            (c.get("field"), c.get("op"), str(c.get("value")))
            for c in r.get("when", []))))
        sig.setdefault(key, []).append(r)
    for key, group in sig.items():
        if len(group) > 1:
            decs = {(g.get("then") or {}).get("decision") for g in group}
            if len(decs) > 1:
                issues.append({
                    "type": "contradiction",
                    "rules": [g.get("id") for g in group],
                    "desc": f"条件完全相同但结论不同: {sorted(d for d in decs if d)}",
                    "fix": "相同条件必须给出相同结论,否则求值结果取决于规则顺序(不可预期)",
                })
    # 空覆盖:规则引用的对象若不在本体中,由调用方补充校验(此处只做规则集自洽)
    return {"total": len(rules), "issues": issues, "healthy": not issues,
            "note": "静态校验:只查规则集自身矛盾,不依赖数据;通过不代表规则业务上正确"}
