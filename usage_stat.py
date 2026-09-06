#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本体使用度统计 —— DR-026。

《本体智能研究报告》阶段六:"定期评估本体的**业务调用频次**与决策支撑效果,
以使用数据驱动优化迭代,形成『建设—应用—反馈—优化』的反馈流程。"

价值不在于统计本身,而在于**反向指导建模**——把使用数据与证据状态交叉,
能直接产出建模优先级:

  高频 × 低证据(candidate)  → 优先补数据裁决:业务天天在用,证据却不足
  零调用 × 已建模             → 建模过度的嫌疑:下一轮可考虑裁剪
  高频 × 已 verified          → 核心资产:变更时需重点回归

埋点规范(与本系统的可用性原则一致):
- **只读旁路**。记录失败一律静默吞掉,绝不因统计问题影响问数主流程。
- **不引入新锁竞争**。落盘走 workdir JSON,与既有运行时产物同一套路径与 gitignore。
- **不记录问句原文**,只记对象键与计数——避免把业务问句沉淀成需要脱敏的资产。
"""
import json
import os
import threading
import time

_LOCK = threading.Lock()


def _path(workdir):
    return os.path.join(workdir, "ont_usage.json")


def _load(workdir):
    try:
        with open(_path(workdir), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def record(workdir, graph, object_keys, kind="query"):
    """记录一次本体调用。object_keys 为本次真实触达的对象键列表。

    失败静默:统计不可用不应导致问数失败——这是旁路,不是主路。
    """
    if not object_keys:
        return
    try:
        with _LOCK:
            d = _load(workdir)
            g = d.setdefault(graph or "_", {})
            for k in set(object_keys):
                e = g.setdefault(k, {"query": 0, "diagnose": 0, "action": 0, "last": ""})
                if kind in e:
                    e[kind] += 1
                e["last"] = time.strftime("%Y-%m-%d %H:%M:%S")
            tmp = _path(workdir) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=1)
            os.replace(tmp, _path(workdir))      # 原子替换,避免读到半截文件
    except Exception:
        pass


def report(workdir, ir, graph):
    """使用度报告 + 建模优先级建议。"""
    usage = _load(workdir).get(graph or "_", {})
    objs = ir.get("objects", [])

    # 关系证据强度按对象聚合:一个对象参与的关系里有多少是已核验的
    if "links" in ir:
        rels, sk, tk = ir["links"], "source", "target"
    else:
        rels, sk, tk = ir.get("relations", []), "source_concept", "target_concept"
    strong, weak = {}, {}
    for r in rels:
        st = (r.get("status") or "")
        for side in (r.get(sk), r.get(tk)):
            if not side:
                continue
            (strong if st in ("verified", "asserted") else weak)[side] = \
                (strong if st in ("verified", "asserted") else weak).get(side, 0) + 1

    rows = []
    for i, o in enumerate(objs):
        key = o.get("id") or o.get("name") or f"_obj{i}"
        u = usage.get(key, {})
        total = sum(u.get(k, 0) for k in ("query", "diagnose", "action"))
        rows.append({
            "key": key, "cn": o.get("cn") or o.get("name") or key,
            "table": o.get("table") or "",
            "calls": total, "detail": {k: u.get(k, 0) for k in ("query", "diagnose", "action")},
            "last": u.get("last", ""),
            "strong_rels": strong.get(key, 0), "weak_rels": weak.get(key, 0),
        })
    rows.sort(key=lambda x: -x["calls"])

    called = [r for r in rows if r["calls"] > 0]
    hi = called[:max(1, len(called) // 3)] if called else []      # 使用频次前三分之一视为高频
    advice = []
    for r in hi:
        if r["weak_rels"] > 0:
            advice.append({
                "priority": "high", "object": r["key"], "cn": r["cn"],
                "reason": f"高频使用({r['calls']} 次)但仍有 {r['weak_rels']} 条候选关系",
                "action": "优先对该对象的候选关系做数据裁决或人审——业务天天在用,证据却不足",
            })
    zero = [r for r in rows if r["calls"] == 0]
    if zero and called:
        advice.append({
            "priority": "low", "object": None, "cn": None,
            "reason": f"{len(zero)}/{len(rows)} 个对象自统计以来零调用",
            "action": "疑似建模过度,下一轮可评估裁剪;但需先确认统计窗口足够长",
        })
    # 指标使用度(DR-054):键以 metric: 开头;高频 × candidate 的指标优先补核验/人工确认口径
    mstat = {}
    for arr in ((ir.get("metric_layers") or {}).values() if isinstance(ir.get("metric_layers"), dict) else []):
        for m in (arr or []):
            if isinstance(m, dict) and m.get("name"):
                mstat[m["name"]] = m.get("status") or ("candidate" if m.get("candidate", True) else "")
    mrows = []
    for k, u in usage.items():
        if not k.startswith("metric:"):
            continue
        name = k[7:]
        mrows.append({"name": name, "calls": sum(u.get(x, 0) for x in ("query", "diagnose", "action")),
                      "status": mstat.get(name, "unknown"), "last": u.get("last", "")})
    mrows.sort(key=lambda x: -x["calls"])
    for r in mrows[:max(1, len(mrows) // 3)] if mrows else []:
        if r["status"] in ("candidate", "unknown") and r["calls"] > 0:
            advice.append({
                "priority": "high", "object": None, "cn": r["name"], "metric": r["name"],
                "reason": f"指标「{r['name']}」被问到 {r['calls']} 次,口径仍为 {r['status']}",
                "action": "补参照 SQL/参考基准重跑核验,或由业务确认口径后置 certified——口径未核验却高频使用,风险最高",
            })
    return {
        "graph": graph, "objects": len(rows),
        "metrics": mrows[:20],
        "called": len(called), "uncalled": len(rows) - len(called),
        "coverage": round(len(called) * 100.0 / len(rows), 1) if rows else 0.0,
        "top": rows[:20], "advice": advice,
        "note": "调用统计为只读旁路,自埋点起累计;零调用不等于无用——"
                "须结合统计窗口长度判断,窗口过短时不应据此裁剪",
    }
