#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""能力问题(Competency Questions)核验 —— DR-024。

本模块对外称「验收问题」;CQ / Competency Questions 是同一概念的学界叫法,
文件名与字段名沿用 cq 以免破坏既有接口,展示文案一律不出现 CQ。

在**已建成的本体 IR** 上做确定性的结构可达性判定,回答一个记分卡回答不了的问题:
「这个本体到底能不能支撑当初要解决的业务问题」。

判定完全不调 LLM:对象在不在、路径通不通、边的状态够不够,都是图上算出来的,
可回放、可解释。这与本系统的反幻觉规范同源——让模型自评"能不能答",
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


def relation_is_strong(relation):
    """验收问题可采用的强关系。

    ``verified`` 只证明数据连接证据成立；若语义复核已经明确失败，就不能继续把它
    用作业务可回答性的证明。旧 IR 没有语义字段时保持兼容，待复核关系仍由质量报告
    提醒；人工 ``asserted`` 表示已经完成业务裁定，优先于模型软标注。
    """
    status = relation.get("status") or relation.get("evidence_status") or ""
    if status == "asserted":
        return True
    if status != "verified":
        return False
    semantic = relation.get("semantic_status") or relation.get("semantic") or ""
    return semantic not in {"fail", "rejected", "disputed"}


def _rels(ir):
    """兼容两种 IR 形状:demo 的 links[source/target] 与构建产物的 relations[source_concept/target_concept]"""
    if "links" in ir:
        return ir["links"], "source", "target"
    return ir.get("relations", []), "source_concept", "target_concept"


def _obj_names(o):
    """一个对象的全部可指代名称——用于把验收问题里的自然语言词锚定到对象。
    app 形状的对象没有 id,只有 name;demo 形状则 id/name/cn/table 都可能被业务人员用来指代。"""
    out = []
    for k in ("id", "name", "cn", "table"):
        v = o.get(k)
        if isinstance(v, str) and v.strip():
            out.append(v.strip())
    for a in (o.get("aliases") or []):          # DR-027 业务别名:业务用语与表名中文往往不同
        if isinstance(a, str) and a.strip():
            out.append(a.strip())
    for t in (o.get("tables") or []):          # app 形状:一个概念可绑多张表
        if isinstance(t, str) and t.strip():
            out.append(t.strip())
    return out


def _key_of(o, idx):
    """对象在关系端点中使用的键:demo 用 id,app 用 name;都缺则退化为序号占位"""
    return o.get("id") or o.get("name") or o.get("cn") or f"_obj{idx}"


def anchor_objects(question, ir):
    """把验收问题文本锚定到本体对象。

    从严匹配:只认**完整出现**在问句里的名称,不做模糊/子串近似——
    近似匹配会把"单"匹配到"工单",制造虚假的 answerable。
    宁可漏匹配并报告未覆盖项，也不可错配后高估本体的可回答范围。
    """
    q = (question or "").lower()
    candidates = []
    for i, o in enumerate(ir.get("objects", [])):
        key = _key_of(o, i)
        for nm in _obj_names(o):
            n = nm.lower()
            # 中文名 ≥2 字、英文标识 ≥3 字符才参与匹配,避免单字/短码误命中
            if not (len(n) >= 2 if re.search(r"[一-鿿]", n) else len(n) >= 3):
                continue
            start = q.find(n)
            while start >= 0:
                candidates.append({
                    "key": key,
                    "matched": nm,
                    "cn": o.get("cn") or o.get("name") or key,
                    "start": start,
                    "end": start + len(n),
                })
                start = q.find(n, start + 1)

    # 同一文本区间优先最长名称。例如“供应商分类”不能再额外命中“供应商”；
    # 但“供应商与供应商分类”中首个独立出现的“供应商”仍会保留。
    survivors = []
    for candidate in candidates:
        nested = any(
            other["key"] != candidate["key"]
            and other["start"] <= candidate["start"]
            and other["end"] >= candidate["end"]
            and (other["end"] - other["start"]) > (candidate["end"] - candidate["start"])
            for other in candidates
        )
        if not nested:
            survivors.append(candidate)

    hits, seen = [], set()
    for candidate in sorted(survivors, key=lambda x: (x["start"], -(x["end"] - x["start"]))):
        if candidate["key"] in seen:
            continue
        hits.append({k: candidate[k] for k in ("key", "matched", "cn")})
        seen.add(candidate["key"])
    return hits


def _adj(ir, strong_only):
    """无向邻接表。strong_only=True 时只保留证据充分的边。"""
    rels, sk, tk = _rels(ir)
    g = {}
    for r in rels:
        if strong_only and not relation_is_strong(r):
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
    """核验单条验收问题。expect 为可选的期望对象名列表(业务方显式声明该问题应涉及哪些对象)。"""
    anchors = anchor_objects(question, ir)
    seen_keys = {a["key"] for a in anchors}

    missing_expected, ambiguous_expected = [], []
    if expect:
        # ``expect`` 是业务方声明的结构化对象范围，不只是存在性断言。按完整名称
        # 精确解析后，将其作为声明锚点参与路径核验；否则自然语言中未出现表名时，
        # 即使业务方已经消歧，系统仍会错误地只留下一个锚点。
        for expected in expect:
            needle = str(expected).strip().lower()
            matches = []
            for i, obj in enumerate(ir.get("objects", [])):
                if any(name.lower() == needle for name in _obj_names(obj)):
                    matches.append((_key_of(obj, i), obj))
            unique = {key: obj for key, obj in matches}
            if not unique:
                missing_expected.append(str(expected))
                continue
            if len(unique) > 1:
                ambiguous_expected.append(str(expected))
                continue
            key, obj = next(iter(unique.items()))
            if key not in seen_keys:
                anchors.append({
                    "key": key,
                    "matched": str(expected),
                    "cn": obj.get("cn") or obj.get("name") or key,
                    "declared": True,
                })
                seen_keys.add(key)

    if missing_expected:
        return {"question": question, "verdict": "unanswerable",
                "anchors": anchors, "path": None,
                "reason": "本体中不存在声明的对象: " + "、".join(missing_expected),
                "fix": "补建模:先让抽取环节产出这些对象,再重新核验"}
    if ambiguous_expected:
        return {"question": question, "verdict": "unanswerable",
                "anchors": anchors, "path": None,
                "reason": "声明的对象名称指代不唯一: " + "、".join(ambiguous_expected),
                "fix": "改用唯一的对象 id/name/table 声明 expect，避免共享别名产生歧义"}

    keys = [a["key"] for a in anchors]
    if not anchors:
        return {"question": question, "verdict": "unanswerable",
                "anchors": [], "path": None,
                "reason": "问句未能锚定到任何本体对象(名称未在本体中出现)",
                "fix": "补建模,或为对象补中文别名使业务用语可被锚定"}
    if len(keys) == 1:
        if expect and len(expect) == 1:
            return {"question": question, "verdict": "answerable",
                    "anchors": anchors, "path": [keys[0]],
                    "reason": "业务方显式声明为单对象问题,无需跨对象路径",
                    "fix": ""}
        return {"question": question, "verdict": "partial",
                "anchors": anchors, "path": [keys[0]],
                "reason": "仅锚定到一个对象，无法证明问句中的其他业务概念均已覆盖",
                "fix": "补充该验收问题的期望对象清单(expect)，或为遗漏对象补中文名/业务别名"}

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
                "reason": "连通路径包含候选、证据不足或语义存疑的关系: " +
                          "、".join(f"{a}↔{b}" for a, b in weak_pairs),
                "fix": "补证据或完成语义人审:只有数据证据成立且语义无争议的 verified/asserted 关系才计入强路径"}
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
    """把不可答/部分可答的验收问题转成待补条目，回流进 IR 的 gaps 清单。"""
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

# ── 穿透链路核验(DR-025)────────────────────────────────────────────
# 《本体智能研究报告》四个行业案例(电力/通信/航空/银行)方法同构:
#   定义 5-6 类核心实体 → 建立一条纵向穿透链路 → 在链路节点上嵌规则与动作
# 例:停电事件—设备—线路—用户;订单—网络资源—工单—用户;飞机—子系统—零部件—供应商。
# 本体的价值不在对象多,而在能否从链路一端穿到另一端。故把「主链路」提升为
# 可声明、可核验的一等公民:逐段核验而非只看首尾连通——首尾通但中段断的链路
# 在业务上是断的(追溯会在断点处失去责任主体),必须逐段判定。

def check_chain(chain, ir):
    """核验一条穿透链路。chain 为对象名列表,按业务顺序排列。

    与 check_one 的区别:验收问题是「这个问题答不答得了」,链路是「这条追溯路径通不通」——
    后者逐段给出断点位置,便于直接定位到该补哪一段。
    """
    if not chain or len(chain) < 2:
        return {"chain": chain, "verdict": "invalid", "reason": "链路至少需 2 个节点"}

    resolved, missing = [], []
    for name in chain:
        hit = None
        for i, o in enumerate(ir.get("objects", [])):
            if any(n.lower() == str(name).strip().lower() for n in _obj_names(o)):
                hit = {"name": name, "key": _key_of(o, i),
                       "cn": o.get("cn") or o.get("name")}
                break
        if hit:
            resolved.append(hit)
        else:
            missing.append(name)

    if missing:
        return {"chain": chain, "verdict": "unanswerable", "missing": missing,
                "segments": [],
                "reason": "链路节点未在本体中找到: " + "、".join(missing),
                "fix": "补建模:这些概念尚未进入本体,链路无法成立"}

    g_strong, g_all = _adj(ir, True), _adj(ir, False)
    segments, weak, broken = [], 0, 0
    for i in range(len(resolved) - 1):
        a, b = resolved[i], resolved[i + 1]
        p = _path(g_strong, a["key"], b["key"])
        if p:
            seg = {"from": a["name"], "to": b["name"], "status": "ok",
                   "hops": len(p) - 1, "path": p}
        else:
            p2 = _path(g_all, a["key"], b["key"])
            if p2:
                weak += 1
                seg = {"from": a["name"], "to": b["name"], "status": "weak",
                       "hops": len(p2) - 1, "path": p2,
                       "note": "路径须借道 candidate/gap 边,证据不足"}
            else:
                broken += 1
                seg = {"from": a["name"], "to": b["name"], "status": "broken",
                       "hops": None, "path": None,
                       "note": "两节点间无任何路径,链路在此断开"}
        segments.append(seg)

    verdict = "broken" if broken else ("weak" if weak else "intact")
    return {
        "chain": chain, "verdict": verdict, "segments": segments,
        "intact_segments": sum(1 for s in segments if s["status"] == "ok"),
        "total_segments": len(segments),
        "reason": {"intact": "全链贯通,各段路径均仅经 verified/asserted 边",
                   "weak": f"{weak} 段须借道候选边(骨架成立、证据不足)",
                   "broken": f"{broken} 段完全断开,追溯将在此失去下游"}[verdict],
        "fix": {"intact": "",
                "weak": "对断点段沿途候选关系做数据裁决或人审升级",
                "broken": "补关系:断开段之间缺少任何路径,须在抽取或人审阶段补建"}[verdict],
    }


def chain_gaps(report):
    """把链路断点转成待补条目。"""
    if report.get("verdict") in ("intact", "invalid"):
        return []
    out = []
    for s in report.get("segments", []):
        if s["status"] == "ok":
            continue
        out.append({
            "type": "chain_broken" if s["status"] == "broken" else "chain_weak",
            "desc": f"穿透链路断点:{s['from']} → {s['to']} —— {s.get('note', '')}",
            "fix": "补关系" if s["status"] == "broken" else "补证据(候选关系待裁决)",
        })
    for m in report.get("missing", []):
        out.append({"type": "chain_missing_node",
                    "desc": f"穿透链路节点缺失:{m} 未在本体中",
                    "fix": "补建模"})
    return out
