#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""问数使用本体的决策层(DR-119)。入口在 server.py 的 /api/chat 与 /api/chat/stream。

各项作用都依赖图谱中的声明;图谱没有对应内容时该项不起作用,问题交回原来的 NL2SQL:

1. 上下文按锚定对象召回:作用对象在锚定子图内的约束与判定规则全部带上;使用提醒先取作用于锚定对象的。
2. 未接入属性拦截:问句包含点亮矩阵 ``lighting.unlit`` 中的属性词(如"实际完工")时不生成 SQL,答复缺少的数据及来源系统。
3. 业务词解析:概念名与别名按最长匹配;图谱声明 ``clock`` 时把时间词换算成日期区间。
4. 问题分型与求解:
     查数      NL2SQL(不在本模块)
     判定      规则条件编译为只读 SQL 计数并取样,样例行再经 rule_engine 复核;不能编译时逐行求值(有上限,超限时标明)
     解释      须命中约束或规则;引用约束参数与依据,使用提醒作补充
     追踪      按 ``main_chain`` 逐跳查询;每一跳须是本体中已验证或人工确认的关系
     异常汇总  规则本全部规则的判定计数
     推演      只考虑作用对象在本图谱中、且必填参数都声明了 ``param_grounding`` 的动作;从问句取参数,调用低风险动作,
               高风险动作只给出参数与动作中心入口;动作没有配置执行器时如实说明只做了登记
5. 每个答案附依据卡(结论、依据、关键值、提示、下一步)。

图谱中的可选声明:
  clock            {"table", "today", "window_end"?}
  main_chain       可省略:没有声明时从图谱的工艺路线(routes)推出,见 ontology_decision.main_chain。
                   {"root": {table, key, label, show[]},
                    "hops": [{table, fk, parent?, pk?, key?, label, order_by?, start?, end?, step?, resource?, show[],
                              lookups: [{table, local, key, show: {列: 显示名}}], flag?: {column, value, label}}],
                    "status": {table, key, show: {列: 显示名}}}
  param_grounding  {参数名: {"table", "key", "match": [列], "label"?, "via": {table, key, column}?} | {"type": "date"}}
  concepts[].aliases、caveats[].objects
"""
from __future__ import annotations

import datetime as dt
import re
import sqlite3

import ontology_decision
import rule_engine

# ------------------------------------------------------------------ 基础
_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-_/.]{0,39}")
_CN_DIGIT = {"零": 0, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_SQL_OPS = {"==": "=", "!=": "<>", ">": ">", ">=": ">=", "<": "<", "<=": "<="}
ROWWISE_CAP = 20000          # 规则不能编译成 SQL 时逐行求值的上限
KEY_CAP = 20000              # 一条规则记录命中键的上限
# 动作执行器返回结构里问数会读的字段(执行器约定见 bp_actions)及其显示名
EXECUTOR_FIELDS = {"promise": "承诺结论", "earliest_finish": "最早完工", "late_days": "相对交期(天,正数为晚)", "recommended": "推荐方案"}


def _objects(ir):
    return [o for o in (ir or {}).get("objects") or [] if isinstance(o, dict)]


def _table_of(o):
    return str(o.get("table") or o.get("name") or "")


def _obj_by_table(ir):
    out: dict = {}
    for o in _objects(ir):
        for t in [o.get("table"), o.get("name")] + list(o.get("tables") or []):
            if t:
                out.setdefault(str(t).lower(), o)
    return out


def _bigrams(s):
    s = re.sub(r"[\s()()0-9A-Za-z_.,,;;:/-]+", "", str(s or ""))
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _ident(s):
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(s or "")))


def _rows(connect, sql, args=(), limit=5000):
    con = connect()
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute(sql, args)
        return [dict(r) for r in cur.fetchmany(limit)]
    finally:
        con.close()


def _scalar(connect, sql, args=()):
    r = _rows(connect, sql, args, 1)
    return list(r[0].values())[0] if r else None


def _has_table(connect, t):
    try:
        return bool(_rows(connect, "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name = ?", (t,), 1))
    except sqlite3.Error:
        return False


def _lit(v):
    return "NULL" if v is None else (str(v) if isinstance(v, (int, float)) else "'" + str(v).replace("'", "''") + "'")


def _contains(text, word):
    """中文按子串;含 ASCII 字母数字的词要求前后不是字母数字(避免 SY 命中 SY01)。"""
    if re.search(r"[A-Za-z0-9]", word):
        return re.search(r"(?<![A-Za-z0-9])" + re.escape(word) + r"(?![A-Za-z0-9])", text) is not None
    return word in text


def _find(text, word, start=0):
    if re.search(r"[A-Za-z0-9]", word):
        m = re.compile(r"(?<![A-Za-z0-9])" + re.escape(word) + r"(?![A-Za-z0-9])").search(text, start)
        return m.start() if m else -1
    return text.find(word, start)


# ------------------------------------------------------------------ 1. 按锚定对象召回约束、规则、提醒
def anchored_objects(ir, anchor_tables):
    idx = _obj_by_table(ir)
    names = set()
    for t in anchor_tables or []:
        o = idx.get(str(t or "").lower().removeprefix("up."))
        if o:
            names |= {str(o.get("name") or ""), _table_of(o)}
    return {n for n in names if n}


def _caveat_objects(c, ir):
    if c.get("objects"):
        return set(c["objects"])
    text = f"{c.get('statement', '')} {c.get('check_sql', '')}"
    hit = set()
    for o in _objects(ir):
        t = _table_of(o)
        if t and re.search(r"(?<![A-Za-z0-9_])" + re.escape(t) + r"(?![A-Za-z0-9_])", text):
            hit.add(t)
        cn = str(o.get("cn") or "")
        if len(cn) >= 3 and cn in text:
            hit.add(t)
    return hit


def caveats_for(ir, anchored, question, limit=12):
    """依次取:作用于锚定对象的提醒、与问句有两个以上字二元组重合的提醒、其余提醒(按原顺序补足,与 DR-088 时
    "全部提醒取前若干条"的做法保持一致)。返回 (提醒, 其中作用于锚定对象的条数)。"""
    # 只取可用的提醒:被否决的、取数对应已失效或尚未重新核验的不带入
    cavs = [c for c in (ir or {}).get("caveats") or [] if isinstance(c, dict) and c.get("statement")
            and c.get("status") != "rejected" and c.get("binding_status") not in ("stale", "unresolved")]
    qb = _bigrams(question)
    first = [c for c in cavs if _caveat_objects(c, ir) & anchored]
    second = [c for c in cavs if c not in first and len(_bigrams(c["statement"]) & qb) >= 2]
    rest = [c for c in cavs if c not in first and c not in second]
    return (first + second + rest)[:limit], len(first)


def decision_context(question, ir, rules, anchored):
    """上下文段落:作用于锚定对象的约束与判定规则(全部,不按词匹配)。返回 (文本, 条数, 用到的 id)。"""
    cons = [c for c in (ir or {}).get("constraints") or [] if isinstance(c, dict) and c.get("on") in anchored]
    rls = [r for r in rules or [] if isinstance(r, dict) and r.get("on") in anchored]
    lines = []
    if cons:
        lines.append("[本体约束(作用于本次锚定的对象;写查询时把它们当作业务事实)]")
        for c in cons:
            p = ";".join(f"{k}={v}" for k, v in (c.get("params") or {}).items())
            lines.append(f"- {c.get('cn') or c.get('id')}({c.get('on')}{'·' + c['applies_to'] if c.get('applies_to') else ''}):"
                         f"{c.get('rule', '')}" + (f" 参数:{p}" if p else ""))
    if rls:
        lines.append("[判定规则(条件 → 结论)]")
        for r in rls:
            lines.append(f"- {r.get('cn') or r.get('id')}({r.get('on')}):{_cond_text(r)} → {(r.get('then') or {}).get('decision', '')}")
    used = {"constraints": [c.get("id") for c in cons], "rules": [r.get("id") for r in rls]}
    return "\n".join(lines), len(cons) + len(rls), used


def _cond_text(r):
    return " 且 ".join(f"{w.get('field')} {w.get('op')} {w.get('value')}" for w in r.get("when") or [] if isinstance(w, dict))


# ------------------------------------------------------------------ 2. 未接入属性拦截
def _unlit_terms(entry):
    attr = str(entry.get("attribute") or "")
    tail = attr.split(".", 1)[1] if "." in attr else attr
    return [t.strip() for t in re.split(r"[/、,,]", tail) if len(t.strip()) >= 2]


def lighting_gate(question, ir):
    """问句包含未接入属性的词 → 返回该条目(含所需数据源);否则 None。"""
    for e in ((ir or {}).get("lighting") or {}).get("unlit") or []:
        if not isinstance(e, dict):
            continue
        hits = [t for t in _unlit_terms(e) if _contains(question or "", t)]
        if hits:
            return {**e, "terms": hits}
    return None


# ------------------------------------------------------------------ 3. 业务词解析:别名与时间词
def _clock(ir, connect):
    """(今天, 来源说明, 分析窗口结束日或 None, 是否来自图谱声明的时钟)。"""
    spec = (ir or {}).get("clock") or {}
    t, col = spec.get("table"), spec.get("today") or "today"
    if t and _ident(t) and _ident(col) and connect and _has_table(connect, t):
        try:
            row = _rows(connect, f'SELECT * FROM "{t}" LIMIT 1', limit=1)
            if row and row[0].get(col):
                today = dt.date.fromisoformat(str(row[0][col])[:10])
                we = row[0].get(spec.get("window_end") or "window_end")
                try:
                    we = dt.date.fromisoformat(str(we)[:10]) if we else None
                except ValueError:
                    we = None
                return today, f"{t}.{col}", we, True
        except (sqlite3.Error, ValueError):
            pass
    return dt.date.today(), "系统日期", None, False


def _month_range(d, add):
    y, m = d.year + (d.month - 1 + add) // 12, (d.month - 1 + add) % 12 + 1
    first = dt.date(y, m, 1)
    nxt = dt.date(y + (m // 12), m % 12 + 1, 1)
    return first, nxt - dt.timedelta(days=1)


def _num(s):
    """阿拉伯数字或 99 以内的中文数字;"半"为 0.5。"""
    s = str(s or "")
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return float(s)
    if s == "半":
        return 0.5
    if s in _CN_DIGIT:
        return _CN_DIGIT[s]
    m = re.fullmatch(r"([一二两三四五六七八九]?)十([一二三四五六七八九]?)", s)
    if m:
        return (_CN_DIGIT.get(m[1], 1) if m[1] else 1) * 10 + (_CN_DIGIT.get(m[2], 0) if m[2] else 0)
    return None


def time_words(question, today, window_end=None):
    q, out = question or "", []

    def add(word, a, b):
        out.append({"word": word, "from": a.isoformat(), "to": b.isoformat()})
    if re.search(r"今天|今日", q):
        add("今天", today, today)
    if "明天" in q:
        add("明天", today + dt.timedelta(1), today + dt.timedelta(1))
    mon = today - dt.timedelta(days=today.weekday())
    if re.search(r"本周|这周|这一周", q):
        add("本周", mon, mon + dt.timedelta(6))
    if re.search(r"下周|下一周", q):
        add("下周", mon + dt.timedelta(7), mon + dt.timedelta(13))
    if re.search(r"本月|这个月", q):
        add("本月", *_month_range(today, 0))
    if re.search(r"下个月|下月", q):
        add("下个月", *_month_range(today, 1))
    for m in re.finditer(r"(未来|接下来|最近|近)\s*([0-9]+|[一二两三四五六七八九十]{1,3})\s*(天|周|个月)", q):
        n = _num(m.group(2))
        if not n:
            continue
        days = int(n * (1 if m.group(3) == "天" else 7 if m.group(3) == "周" else 30))
        if m.group(1) in ("最近", "近"):            # 过去的一段
            add(m.group(0), today - dt.timedelta(days), today)
        else:
            add(m.group(0), today, today + dt.timedelta(days))
    if window_end and re.search(r"(演示|分析)?窗口", q):
        add("分析窗口", today, window_end)
    return out


def aliases(question, ir):
    """概念名与别名的最长匹配(已匹配的片段不再参与较短的匹配)。返回 [(词, 概念)]。"""
    cands = []
    for c in (ir or {}).get("concepts") or []:
        if not isinstance(c, dict) or not c.get("table"):
            continue
        names = [str(c.get("name") or "").split(":")[-1]] + [str(a) for a in c.get("aliases") or []]
        for n in names:
            if len(n) >= 2:
                cands.append((n, c))
    cands.sort(key=lambda x: -len(x[0]))
    q = question or ""
    taken: list = []
    out: list = []
    for n, c in cands:
        i = _find(q, n)
        while i >= 0:
            span = (i, i + len(n))
            if not any(a < span[1] and span[0] < b for a, b in taken):
                taken.append(span)
                if not any(x[1] is c for x in out):
                    out.append((n, c))
                break
            i = _find(q, n, i + 1)
    return out


def alias_masked(question, ir):
    """供意图核对(DR-073)使用的问句:按别名解析出的片段(连同紧随的"工序 / 工位 / 任务")替换为概念所在的表名。
    例如"干燥工序"在本体中是工序任务里 task_number = 'GZ' 的行,不替换的话意图核对会把它当成工序主数据表。"""
    q = question or ""
    for word, c in aliases(q, ir):
        if not c.get("filter"):
            continue
        pat = (r"(?<![A-Za-z0-9])" + re.escape(word) + r"(?![A-Za-z0-9])") if re.search(r"[A-Za-z0-9]", word) else re.escape(word)
        q = re.sub(pat + r"(工序|工位|任务)?", f" {c['table']} ", q, count=1)
    return q


def resolve_terms(question, ir, connect):
    today, src, we, declared = _clock(ir, connect)
    tw = time_words(question, today, we) if declared else []      # 未声明时钟的图谱不给时间提示
    al = aliases(question, ir)
    lines = []
    if tw or al:
        lines.append("[问句中的业务词解析(按本体;写查询时直接使用)]")
    for t in tw:
        lines.append(f"- 「{t['word']}」= {t['from']} 至 {t['to']}(今天按 {src} = {today.isoformat()})")
    for word, c in al:
        lines.append(f"- 「{word}」→ {c.get('name')}:{c['table']}" + (f" WHERE {c['filter']}" if c.get("filter") else "")
                     + (f"(列 {c['column']})" if c.get("column") else ""))
    return "\n".join(lines), {"today": today.isoformat(), "clock": src, "time": tw,
                              "aliases": [{"word": w, "concept": c.get("name"), "filter": c.get("filter")} for w, c in al]}


# ------------------------------------------------------------------ 4. 问题分型
_WHATIF = re.compile(
    r"(能否|能不能|可不可以|可否|是否能|能够|来得及)[^。?？]{0,10}(承诺|按期|交付|交货|插|排进|接单|完成)"
    r"|插单|插进|插入一张"
    r"|(如果|假如|要是|假设|万一)[^。?？]{0,24}(停机|停了|坏了|修不好|不修|故障|插|新增|新来|加一张|来一张|延期|推迟)"
    r"|(修不好|不修了|一直停|停机|停了|坏了|故障)[^。?？]{0,12}(影响|波及|会怎样)"
    r"|新增(?!了)[^。?？]{0,16}订单[^。?？]{0,24}(能否|能不能|会|影响|行不行|可以吗|来得及)"
    r"|新来[^。?？]{0,10}订单")
_LOOKUP_CUE = re.compile(r"几次|次数|记录|历史|时长|上周|上个月|上月|去年|昨天|前天|统计|汇总")   # 问已发生的事,不是推演
_TRACE = re.compile(r"到哪|进度|哪道工序|卡在|走到|执行到|现在在|当前在|做到|进行到")
_EXPLAIN = re.compile(r"为什么|为何|依据是什么|怎么算|如何计算|是怎么得出|凭什么|怎么判定|规则是什么")   # "原因是什么"常是问备注字段,不算
_CAUSAL = re.compile(r"下降|上升|增长|减少|变化|波动|比[^。?？]{0,8}(高|低|多|少)")             # 问数据变化的原因,交给查数
_JUDGE = re.compile(r"哪些|有没有|是否|名单|有哪")
_RANK = re.compile(r"最|前\s*\d|top|TOP|排名|排序|平均|合计|总计|总共|一共|共有|多少|几[批个张台项条次道]|数量|占比|趋势|统计")
_MONITOR = re.compile(r"(有哪些|有什么|有没有|新增|最新)[^。?？]{0,4}(异常|告警|预警|风险)")
_SYN = [(re.compile(r"修不好|坏了|停了|不修|故障|停机"), "停机 影响"),
        (re.compile(r"承诺|按期|交期|来得及|接单|插单|新增.{0,16}订单"), "插单 试算 承诺"),
        (re.compile(r"方案|对比|怎么插|怎么排"), "方案 对比")]
_GENERIC = {"工序", "订单", "机台", "任务", "物料", "为什", "什么", "怎么", "多少", "哪些", "时间"}
ACTION_MIN_SCORE = 4          # 至少两个字二元组与动作名重合(同义词扩展计入)


def _single_ask(q):
    """只问一件事:一个分句,且没有"和 / 及 / 分别 / 对应"等追加要求。"""
    parts = [p for p in re.split(r"[,,;;??？。]", q or "") if p.strip()]
    return len(parts) == 1 and not re.search(r"和|及|并且|分别|对应|以及", q or "")


def _root_ids(question, ir, connect):
    mc = ontology_decision.main_chain(ir or {})
    root = mc.get("root") or {}
    t, k = root.get("table"), root.get("key")
    if not (t and k and _ident(t) and _ident(k) and connect and _has_table(connect, t)):
        return []
    toks = [x.strip("./-_") for x in _CODE.findall(question or "")]
    toks = [x for x in dict.fromkeys(toks) if re.search(r"\d", x) and len(x) >= 2][:6]
    out = []
    for x in toks:
        try:
            if _rows(connect, f'SELECT 1 FROM "{t}" WHERE "{k}" = ? LIMIT 1', (x,), 1):
                out.append(x)
        except sqlite3.Error:
            return []
    return out


def match_rule(question, rules):
    """去掉泛用词后,问句与规则名重合的字二元组最多(至少 2 个)的规则。"""
    qb = _bigrams(question) - _GENERIC
    best, score = None, 1
    for r in rules or []:
        s = len(_bigrams(re.sub(r"[((].*?[))]", "", str(r.get("cn") or ""))) & qb)
        if s > score:
            best, score = r, s
    return best


def bound_actions(actions, ir):
    """可用于推演的动作:已启用、作用对象在本图谱中、所有必填参数都在 param_grounding 中有声明。"""
    pg = (ir or {}).get("param_grounding") or {}
    if not pg:
        return []
    idx = _obj_by_table(ir)
    out = []
    for a in actions or []:
        if a.get("enabled") is False:
            continue
        objs = [str(a.get(k) or "").lower() for k in ("object_table", "object")]
        if not any(o and o in idx for o in objs):
            continue
        if all(p.get("name") in pg for p in a.get("params") or [] if p.get("required")):
            out.append(a)
    return out


def match_action(question, actions, synonyms=True, min_score=ACTION_MIN_SCORE):
    q = question or ""
    for rx, extra in (_SYN if synonyms else []):
        if rx.search(q):
            q += " " + extra
    qb = _bigrams(q)
    best, score = None, 0
    for a in actions or []:
        if a.get("enabled") is False:
            continue
        s = 2 * len(_bigrams(a.get("cn")) & qb) + len(_bigrams(a.get("desc")) & qb)
        if s > score:
            best, score = a, s
    return best if score >= min_score else None


def classify(question, ir, rules, actions, connect):
    """确定性分型,返回 {"type": ..., ...}。actions 为全部动作类型,推演只考虑与本图谱绑定且参数都有接地声明的。"""
    q = question or ""
    gate = lighting_gate(q, ir)
    if gate:
        return {"type": "unlit", "gate": gate}
    if _WHATIF.search(q) and not _EXPLAIN.search(q) and not _LOOKUP_CUE.search(q):
        act = match_action(q, bound_actions(actions, ir))
        if act:
            return {"type": "whatif", "action": act}
    ids = _root_ids(q, ir, connect) if _TRACE.search(q) else []
    if ids and _single_ask(q):
        return {"type": "trace", "root_id": ids[0]}
    if _EXPLAIN.search(q):
        return {"type": "explain"}
    if _MONITOR.search(q) and rules:
        return {"type": "monitor"}
    if _JUDGE.search(q) and not _RANK.search(q) and _single_ask(q):
        r = match_rule(q, rules)
        if r:
            return {"type": "judge", "rule": r}
    return {"type": "lookup"}


# ------------------------------------------------------------------ 模型辅助分型(DR-123)
ASSIST_PROMPT = """判断下面的问题属于哪一类。只能从给出的选项里选一个;拿不准,或者问的是排名、汇总、统计、对比,一律选 lookup。
问题:{question}
选项:
{options}
只输出 JSON:{{"choice": "选项编号"}}"""


def assist_options(question, ir, rules, actions, connect):
    """这张图谱上除查数之外还能怎么答:每条规则可判定,每条约束可解释,问句里有主线上的编号可追踪,
    有规则可汇总异常。返回 [(编号, 说明, 路由)]。没有决策层内容时为空。"""
    opts = []
    for r in (rules or [])[:12]:
        if isinstance(r, dict) and r.get("id"):
            opts.append((f"judge:{r['id']}", f"按规则「{r.get('cn') or r['id']}」列出命中的记录", {"type": "judge", "rule": r}))
    for c in ((ir or {}).get("constraints") or [])[:12]:
        if isinstance(c, dict) and c.get("id"):
            opts.append((f"explain:{c['id']}", f"解释约束「{c.get('cn') or c['id']}」的内容与依据",
                         {"type": "explain", "pinned": [c["id"]]}))
    if rules:
        opts.append(("monitor", "汇总当前各条规则命中了多少(有哪些异常)", {"type": "monitor"}))
    ids = _root_ids(question, ir, connect)
    if ids:
        opts.append(("trace", f"追踪 {ids[0]} 的执行进度", {"type": "trace", "root_id": ids[0]}))
    # 试算不在选项里(DR-125):推演会调用执行器,只由明确的假设性问法触发,不交给模型判断
    return opts


def classify_assisted(question, ir, rules, actions, connect, ask):
    """固定词表没认出来时,请模型在这张图谱实际声明的选项里选一个(DR-123)。

    模型只做选择:选项由 assist_options 确定性地列出,选中之后仍由确定性求解作答;
    模型没有回答、回答不在选项里、或选了 lookup,都按查数处理。ask(prompt) 返回模型的文字或 None。
    一句问几件事的问题不走这条路(与固定词表的做法一致)。
    """
    if not _single_ask(question or "") or lighting_gate(question or "", ir):
        return {"type": "lookup"}
    opts = assist_options(question, ir, rules, actions, connect)
    if not opts:
        return {"type": "lookup"}
    listing = "\n".join(f"- {k}: {text}" for k, text, _ in opts) + "\n- lookup: 其他(查数据、排名、汇总、统计)"
    reply = ask(ASSIST_PROMPT.format(question=str(question or "")[:300], options=listing))
    m = re.search(r'"choice"\s*:\s*"([^"]{1,80})"', str(reply or ""))
    choice = m.group(1).strip() if m else ""
    route = next((dict(r) for k, _t, r in opts if k == choice), None)
    if route is None:
        return {"type": "lookup"}
    route["assisted"] = True
    return route


# ------------------------------------------------------------------ 规则求值
def rules_on_tables(rules, ir):
    """规则按对象登记(on 为对象名或表名);求值在对象的表上进行,这里统一换成表名。"""
    idx = _obj_by_table(ir)
    out = []
    for r in rules or []:
        if not isinstance(r, dict):
            continue
        o = idx.get(str(r.get("on") or "").lower())
        out.append(dict(r, on=_table_of(o)) if o and _table_of(o) else r)
    return out


def rule_sql(rule):
    """规则条件编译成只读 SQL;有无法编译的条件时返回 None。"""
    t = rule.get("on")
    if not _ident(t):
        return None
    where = []
    for w in rule.get("when") or []:
        if not isinstance(w, dict):
            return None
        f, op = w.get("field"), w.get("op")
        if not _ident(f):
            return None
        if op in _SQL_OPS:
            where.append(f'"{f}" {_SQL_OPS[op]} {_lit(w.get("value"))}')
        elif op == "exists":
            where.append(f"(\"{f}\" IS NOT NULL AND \"{f}\" <> '')")
        elif op == "missing":
            where.append(f"(\"{f}\" IS NULL OR \"{f}\" = '')")
        else:
            return None
    return f'SELECT * FROM "{t}"' + (" WHERE " + " AND ".join(where) if where else "")


def evaluate_rule(rule, connect, sample=500, key_cols=None):
    """规则在其表上求值。返回 dict:sql(可编译时)、count、total、rows(样例,最多 sample 行)、keys(key_cols 给出时
    为全部命中行的键,最多 KEY_CAP 个)、trace(一条命中的条件轨迹)、method、truncated、mismatch。
    可编译时以 SQL 计数,样例行再经 rule_engine 复核(不一致时记 mismatch);不可编译时逐行求值,最多 ROWWISE_CAP 行。
    表不存在时返回 None。"""
    t = rule.get("on")
    if not _ident(t) or not _has_table(connect, t):
        return None
    total = _scalar(connect, f'SELECT COUNT(*) FROM "{t}"') or 0
    sql = rule_sql(rule)
    cols = [c for c in key_cols or [] if _ident(c)]
    out = {"sql": sql, "total": total, "truncated": False, "mismatch": False, "keys": None}
    if sql:
        out["method"] = "sql"
        out["count"] = _scalar(connect, f"SELECT COUNT(*) FROM ({sql})") or 0
        out["rows"] = _rows(connect, sql, limit=sample)
        out["mismatch"] = any(not rule_engine.evaluate([rule], t, r)["fired_count"] for r in out["rows"])
        if cols:
            ks = _rows(connect, f"SELECT {', '.join(chr(34) + c + chr(34) for c in cols)} FROM ({sql})", limit=KEY_CAP + 1)
            out["keys"] = ["/".join(str(r.get(c)) for c in cols) for r in ks[:KEY_CAP]]
            out["truncated"] = len(ks) > KEY_CAP
    else:
        out["method"] = "rowwise"
        allrows = _rows(connect, f'SELECT * FROM "{t}"', limit=ROWWISE_CAP)
        hit = [r for r in allrows if rule_engine.evaluate([rule], t, r)["fired_count"]]
        out.update(count=len(hit), rows=hit[:sample], truncated=total > ROWWISE_CAP)
        if cols:
            out["keys"] = ["/".join(str(r.get(c)) for c in cols) for r in hit[:KEY_CAP]]
    first = out["rows"][0] if out["rows"] else None
    out["trace"] = (rule_engine.evaluate([rule], t, first)["fired"] or [{}])[0].get("trace") if first else None
    return out


def _key_cols(connect, table):
    try:
        cols = [r["name"] for r in _rows(connect, f'PRAGMA table_info("{table}")')]
    except sqlite3.Error:
        return []
    keys = [c for c in cols if c.endswith(("_number", "_no", "_id", "_index", "_start"))][:3]
    return keys or cols[:2]


def monitor_snapshot(rules, connect):
    """全部规则(on 须已换成表名)的命中:{rule_id: {"cn", "severity", "on", "decision", "count", "total", "keys", "truncated"}}。
    表不存在的规则不列出。"""
    out = {}
    for r in rules or []:
        if not isinstance(r, dict) or not r.get("id") or not _ident(r.get("on")):
            continue
        ev = evaluate_rule(r, connect, sample=1, key_cols=_key_cols(connect, r["on"]))
        if ev is None:
            continue
        out[r["id"]] = {"cn": r.get("cn"), "severity": (r.get("then") or {}).get("severity") or "medium", "on": r.get("on"),
                        "decision": (r.get("then") or {}).get("decision"), "count": ev["count"], "total": ev["total"],
                        "keys": ev["keys"] or [], "truncated": ev["truncated"]}
    return out


# ------------------------------------------------------------------ 依据卡
_TYPE_CN = {"lookup": "查数", "judge": "判定", "whatif": "推演", "explain": "解释", "trace": "追踪", "unlit": "数据未接入",
            "monitor": "异常汇总", "clarify": "需补充信息"}


def _card(type_, conclusion, basis=(), kv=(), next_=(), flags=(), links=()):
    return {"type": type_, "type_cn": _TYPE_CN.get(type_, type_), "conclusion": conclusion, "basis": [b for b in basis if b],
            "kv": [list(x) for x in kv], "next": list(next_), "flags": list(flags), "links": list(links)}


def _constraint_basis(c):
    ev = "、".join(f"{e.get('source')}:{e['lines']}" if e.get("lines") else str(e.get("source") or "")
                  for e in c.get("evidence") or [] if isinstance(e, dict))
    return {"kind": "constraint", "id": c.get("id"), "cn": c.get("cn"), "text": c.get("rule"), "params": c.get("params") or {},
            "evidence": ev}


def _rule_basis(r, count=None, trace=None):
    return {"kind": "rule", "id": r.get("id"), "cn": r.get("cn"), "text": f"{_cond_text(r)} → {(r.get('then') or {}).get('decision', '')}",
            "count": count, "trace": trace, "on": r.get("on")}


def _next_for_rules(fired_rules, actions):
    out = []
    for r in fired_rules:
        act = (r.get("then") or {}).get("action")
        a = match_action(f"{r.get('cn')} {(r.get('then') or {}).get('decision', '')} {act or ''}", actions, synonyms=False, min_score=2)
        if a:
            out.append({"kind": "action", "label": f"{a['cn']}({'需审批' if a.get('risk') == 'high' else '低风险'})",
                        "action_id": a["id"], "href": "#actioncenter"})
    return list({x["action_id"]: x for x in out}.values())[:3]


def lookup_card(question, results, ir, used, metric_cards, anchored, caveats_used, rules, actions, narrative=""):
    sql_text = " ".join(str(r.get("sql") or "") for r in results or [])
    in_sql = {t for t in anchored if re.search(r"(?<![A-Za-z0-9_])" + re.escape(t) + r"(?![A-Za-z0-9_])", sql_text)}
    # 约束、点亮矩阵按对象名登记,SQL 里出现的是表名:表出现了,对象的各种指代都算出现
    for o in _objects(ir):
        if _table_of(o) in in_sql:
            in_sql |= {str(o[k]) for k in ("id", "name", "table") if o.get(k)}
    basis = [{"kind": "sql", "title": r.get("title"), "text": r.get("sql")} for r in results or []]
    cons = {c.get("id"): c for c in (ir or {}).get("constraints") or [] if isinstance(c, dict)}
    basis += [_constraint_basis(cons[i]) for i in used.get("constraints") or [] if i in cons and cons[i].get("on") in in_sql][:6]
    basis += [{"kind": "caveat", "text": c["statement"]} for c in caveats_used[:4]]
    flags = []
    for m in metric_cards or []:
        basis.append({"kind": "metric", "name": m.get("name"), "text": m.get("caliber") or m.get("desc"), "status": m.get("status")})
        if m.get("status") not in ("certified", "verified", "confirmed"):
            flags.append(f"指标「{m.get('name')}」口径未核验(状态 {m.get('status') or '未标注'}),数值仅供参考")
    lit = ((ir or {}).get("lighting") or {}).get("objects") or {}
    for t in sorted(in_sql):
        e = lit.get(t) or {}
        if e.get("status") == "partial":
            flags.append(f"对象 {t} 的数据只部分接入:{e.get('source', '')}")
    related = [r for r in rules_on_tables(rules, ir) if r.get("on") in in_sql]
    nxt = [{"kind": "question", "label": f"哪些{r.get('cn')}?", "q": f"有哪些{re.sub(r'[((].*?[))]', '', r.get('cn') or '')}?"}
           for r in related[:2]]
    nxt += _next_for_rules(related, actions)
    concl = (narrative or "").strip().split("\n")[0][:160] or (f"共 {len(results[0]['data']['rows'])} 行" if results else "")
    return _card("lookup", concl, basis, next_=nxt, flags=flags)


# ------------------------------------------------------------------ 各类求解
def _result(title, sql, rows):
    cols = list(rows[0].keys()) if rows else []
    return {"title": title, "sql": sql or "", "chart": {"type": "table"}, "data": {"columns": cols, "rows": rows[:500]}}


def run_unlit(route, question):
    g = route["gate"]
    text = (f"问到的「{'、'.join(g['terms'])}」对应的数据还没有接入:{g.get('attribute')}。需要接入 {g.get('needs')} 才有这项数据"
            + (f"({g['why']})" if g.get("why") else "") + "。没有用计划值代替实际值作答。")
    alt = []
    if "实际" in (question or ""):
        alt.append({"kind": "question", "label": "改问计划值", "q": question.replace("实际", "计划")})
    card = _card("unlit", text, [{"kind": "lighting", "text": g.get("attribute"), "needs": g.get("needs"), "why": g.get("why")}],
                 next_=alt, links=[{"label": "数据接入情况", "href": "#graph"}])
    return {"results": [], "narrative": text, "evidence_card": card}


def run_judge(route, connect, actions, ir, validate):
    r = route["rule"]
    ev = evaluate_rule(r, connect)
    if ev is None:                                   # 规则作用的表不在数据源中:交回查数
        return None
    shown = ev["sql"] or f"逐行求值 {r.get('on')}(规则含无法编译为 SQL 的条件)"
    if ev["sql"] and validate:
        ok, why = validate(ev["sql"])
        if not ok:                                   # 本体约束校验(有效行、租户等)不通过:交回查数,由 NL2SQL 按原因修正
            return None
    sev = (r.get("then") or {}).get("severity")
    scope = f"{ev['total']} 行中" + (f"(逐行求值只检查了前 {ROWWISE_CAP} 行)" if ev["truncated"] else "")
    text = (f"按规则「{r.get('cn')}」({r.get('id')})在 {r.get('on')} 上判定:{scope} {ev['count']} 行命中,"
            f"结论「{(r.get('then') or {}).get('decision', '')}」(严重度 {sev})。")
    flags = ["规则 SQL 的结果与逐行求值不一致,请核对规则条件的取值类型"] if ev["mismatch"] else []
    if ev["count"] > len(ev["rows"]):
        flags.append(f"下表只列出前 {len(ev['rows'])} 行")
    nxt = _next_for_rules([r], actions)
    root = (ontology_decision.main_chain(ir or {})).get("root") or {}
    if ev["rows"] and root.get("key") in ev["rows"][0]:        # 沿主线追踪第一条命中
        rid = ev["rows"][0][root["key"]]
        nxt.insert(0, {"kind": "question", "label": f"追踪 {rid}", "q": f"{root.get('label') or ''} {rid} 现在到哪了?"})
    card = _card("judge", text, [_rule_basis(r, ev["count"], ev["trace"]), {"kind": "sql", "text": shown}],
                 kv=[("判定对象", r.get("on")), ("总行数", ev["total"]), ("命中", ev["count"])], next_=nxt, flags=flags)
    return {"results": [_result(f"{r.get('cn')}:命中 {ev['count']} 行", shown, ev["rows"])], "narrative": text, "evidence_card": card}


def run_monitor(rules, connect, actions, ir):
    snap = monitor_snapshot(rules, connect)
    rows = [{"规则": v["cn"], "对象": v["on"], "命中": v["count"], "严重度": v["severity"], "结论": v["decision"]}
            for v in sorted(snap.values(), key=lambda v: ({"high": 0, "medium": 1, "low": 2}.get(v["severity"], 3), -v["count"]))]
    hot = [r for r in rows if r["命中"]]
    text = ("按规则本逐条判定,当前有 " + ";".join(f"{r['规则']} {r['命中']} 项" for r in hot) + "。") if hot else "按规则本逐条判定,当前没有命中。"
    fired = [r for r in rules if snap.get(r.get("id"), {}).get("count")]
    card = _card("monitor", text, [_rule_basis(r, snap[r["id"]]["count"]) for r in fired],
                 next_=_next_for_rules(fired, actions), links=[{"label": "规则本", "href": "#graph"}])
    return {"results": [_result("规则判定汇总", "", rows)], "narrative": text, "evidence_card": card}


def run_explain(question, ir, rules, anchored, pinned=()):
    """须命中约束或规则才作答;问数据变化原因("为什么下降了")时,只有概念或别名直接指向约束才作答。
    pinned 是已经确定要解释的约束 id(模型辅助分型选中的),直接算命中。"""
    qb = _bigrams(question) - _GENERIC
    hits = [(n, c) for c in (ir or {}).get("concepts") or [] if isinstance(c, dict)        # 解释不消耗片段:问句提到的概念都算
            for n in [next((x for x in [str(c.get("name") or "").split(":")[-1]] + [str(a) for a in c.get("aliases") or []]
                            if len(x) >= 2 and _contains(question or "", x)), None)] if n]
    al = {c.get("filter") for _, c in aliases(question, ir)}
    linked = {str((c.get("evidence") or {}).get("source")) for _, c in hits if isinstance(c.get("evidence"), dict)}
    scored = []
    for c in (ir or {}).get("constraints") or []:
        if not isinstance(c, dict):
            continue
        s = len(_bigrams(f"{c.get('cn')} {c.get('rule')} {' '.join(map(str, (c.get('params') or {}).keys()))}") & qb)
        direct = (bool(c.get("applies_to")) and any(f and re.search("'" + re.escape(str(c["applies_to"])) + "(['-])", f) for f in al)) \
            or c.get("id") in linked
        if direct or c.get("id") in pinned:
            s += 3
            direct = True
        if s >= 2 and (direct or not _CAUSAL.search(question or "")):
            scored.append((s, c))
    top = max((x[0] for x in scored), default=0)
    cons = [c for sc, c in sorted(scored, key=lambda x: -x[0]) if sc >= top * 0.6][:3]
    rls = [] if _CAUSAL.search(question or "") else \
        [r for r in rules or [] if len(_bigrams(f"{r.get('cn')} {(r.get('then') or {}).get('decision', '')}") & qb) >= 2][:3]
    if not (cons or rls):
        return None
    cavs = sorted((c for c in (ir or {}).get("caveats") or [] if isinstance(c, dict) and len(_bigrams(c.get("statement")) & qb) >= 1),
                  key=lambda c: -len(_bigrams(c.get("statement")) & qb))[:2]
    defs = [c for _, c in hits if str((c.get("evidence") or {}).get("source")) in {x.get("id") for x in cons}]
    parts = [f"{c.get('name')}:{c.get('definition')}" for c in defs[:2]]
    parts += [f"{c.get('cn')}:{c.get('rule')}" + (f"(依据 {_constraint_basis(c)['evidence']})" if c.get("evidence") else "") for c in cons]
    parts += [f"规则「{r.get('cn')}」:{_rule_basis(r)['text']}" for r in rls]
    parts += [f"使用提醒:{c['statement']}" for c in cavs]
    text = "按本体中的约束与规则:\n" + "\n".join(f"- {p}" for p in parts)
    card = _card("explain", parts[0], [_constraint_basis(c) for c in cons] + [_rule_basis(r) for r in rls]
                 + [{"kind": "caveat", "text": c["statement"]} for c in cavs])
    return {"results": [], "narrative": text, "evidence_card": card}


def _verified(ir, child, parent, role=""):
    """两张表对应的对象之间已验证或人工确认的关系;主线的这一跳指定了角色时只认那个角色(DR-126)。"""
    idx = _obj_by_table(ir)
    cn = str((idx.get(child.lower()) or {}).get("name") or child)
    pn = str((idx.get(parent.lower()) or {}).get("name") or parent)
    for r in (ir or {}).get("relations") or []:
        if {r.get("source_concept"), r.get("target_concept")} == {cn, pn} and r.get("status") in ("verified", "asserted", "exists") \
                and (not role or str(r.get("role") or "") == role):
            return r
    return None


def run_trace(route, ir, rules, connect, today, validate=None):
    mc = ontology_decision.main_chain(ir or {})
    root = mc.get("root") or {}
    rid = route["root_id"]
    if not (_ident(root.get("table")) and _ident(root.get("key"))):
        return None
    head = _rows(connect, f'SELECT * FROM "{root["table"]}" WHERE "{root["key"]}" = ?', (rid,), 1)
    label = root.get("label") or "对象"
    if not head:
        return None
    basis, kv, results, lines = [], [(label, rid)], [], []
    kv += [(k, head[0].get(k)) for k in root.get("show") or [] if k in head[0]]
    rows_of = {root["table"]: head}                 # 每张表已取到的行,供后续各跳按声明的父表取键
    prev = root["table"]
    for hop in mc.get("hops") or []:
        t, fk = hop.get("table"), hop.get("fk")
        parent = hop.get("parent") or prev
        pk = hop.get("pk") or (root["key"] if parent == root["table"] else (mc_hop_key(mc, parent) or fk))
        if not (_ident(t) and _ident(fk) and _ident(pk) and parent in rows_of):
            lines.append(f"主线声明不完整({t}),未继续追踪。")
            break
        rel = _verified(ir, t, parent, str(hop.get("role") or ""))
        vals = list(dict.fromkeys(r.get(pk) for r in rows_of[parent] if r.get(pk) is not None))[:500]
        order = f' ORDER BY "{hop["order_by"]}"' if _ident(hop.get("order_by")) else ""
        shown_sql = f'SELECT * FROM "{t}" WHERE "{fk}" IN ({", ".join(_lit(v) for v in vals[:20])}{", …" if len(vals) > 20 else ""}){order}'
        basis.append({"kind": "relation", "text": f"{t}.{fk} → {parent}.{pk}" + (f"（{hop['role']}）" if hop.get("role") else ""),
                      **({"role": hop["role"]} if hop.get("role") else {}),
                      "status": rel.get("status") if rel else "未在本体中验证", "overlap": (rel or {}).get("overlap")})
        if not rel:
            lines.append(f"{t} 与 {parent} 的连接未在本体中验证,未继续追踪。")
            break
        if validate:
            ok, why = validate(shown_sql.replace(", …", ""))
            if not ok:
                return None
        if not vals:
            break
        rows = _rows(connect, f'SELECT * FROM "{t}" WHERE "{fk}" IN ({",".join("?" * len(vals))}){order}', tuple(vals), 2000)
        for lk in hop.get("lookups") or []:
            if not (isinstance(lk, dict) and all(_ident(lk.get(x)) for x in ("table", "local", "key")) and _has_table(connect, lk["table"])):
                continue
            ref = {str(r.get(lk["key"])): r for r in _rows(connect, f'SELECT * FROM "{lk["table"]}"', limit=20000)}
            for r in rows:
                m = ref.get(str(r.get(lk["local"])), {})
                for col, cn in (lk.get("show") or {}).items():
                    r[cn] = m.get(col)
        hop_label = hop.get("label") or t
        if rows and hop.get("start") and hop.get("end"):
            now = today.isoformat() + " 00:00:00"
            for r in rows:
                s, e = str(r.get(hop["start"]) or ""), str(r.get(hop["end"]) or "")
                r["进度"] = "已完成" if e and e <= now else ("进行中" if s and s <= now else "未开工")
            done = sum(1 for r in rows if r["进度"] == "已完成")
            doing = [r for r in rows if r["进度"] == "进行中"]
            todo = [r for r in rows if r["进度"] == "未开工"]
            step_cols = [cn for lk in hop.get("lookups") or [] if isinstance(lk, dict) for cn in (lk.get("show") or {}).values()][:1]
            name_cols = step_cols + [hop.get("step") or ""]
            res_col = hop.get("resource") or ""

            def name(r, cols=name_cols):
                return next((str(r.get(c)) for c in cols if c and r.get(c)), "")

            def res(r, col=res_col):
                return str(r.get(col) or "") if col else ""

            def at(r, col=res_col):
                return f"({r.get(col)})" if col and r.get(col) else ""
            s = f"{hop_label} 共 {len(rows)} 个:已完成 {done}"
            if doing:
                s += ",进行中 " + "、".join(name(r) + at(r) for r in doing[:3])
            if todo:
                s += f",下一道 {name(todo[0])}(计划 {str(todo[0].get(hop['start']))[:16]} 开工" + (f",{res(todo[0])}" if res(todo[0]) else "") + ")"
            elif not doing:
                s += f",全部完成于 {str(rows[-1].get(hop['end']))[:16]}"
            fl = hop.get("flag") or {}
            if fl.get("column"):
                flagged = [r for r in rows if str(r.get(fl["column"])) == str(fl.get("value")) and r["进度"] != "已完成"]
                if flagged:
                    s += f";其中 {len(flagged)} 个{fl.get('label') or '被标记'}" + (f"({'、'.join(sorted({res(r) for r in flagged if res(r)}))})" if res(flagged[0]) else "")
            lines.append(s + "。")
            kv += [("已完成", done), ("进行中", len(doing)), ("未开工", len(todo))]
        elif not rows:
            lines.append(f"没有{hop_label}。")
        extra = [cn for lk in hop.get("lookups") or [] if isinstance(lk, dict) for cn in (lk.get("show") or {}).values()] + ["进度"]
        show = [c for c in list(hop.get("show") or []) + extra if rows and c in rows[0]]
        results.append(_result(f"{rid} 的{hop_label}", shown_sql, [{c: r.get(c) for c in show} for r in rows] if show else rows))
        rows_of[t] = rows
        prev = t
        if not rows:
            break
    if isinstance(mc.get("stopped"), dict):          # 从工艺路线推出的主线没能走到底:说明停在哪、为什么
        sp = mc["stopped"]
        lines.append(f"路线在「{sp.get('after')}」之后无法继续到「{sp.get('before')}」:{sp.get('reason')}。")
    st = mc.get("status") or {}
    if _ident(st.get("table")) and _ident(st.get("key")) and _has_table(connect, st["table"]):
        srow = _rows(connect, f'SELECT * FROM "{st["table"]}" WHERE "{st["key"]}" = ?', (rid,), 1)
        if srow:
            fired = rule_engine.evaluate(rules or [], st["table"], srow[0])["fired"]
            kv += [(cn, srow[0][col]) for col, cn in (st.get("show") or {}).items() if col in srow[0]]
            if fired:
                lines.append("判定:" + ";".join(f"{f['cn']}({f['decision']})" for f in fired) + "。")
            basis += [{"kind": "rule", "id": f["rule"], "cn": f["cn"], "text": f["decision"], "trace": f["trace"]} for f in fired]
    text = f"{label} {rid}:" + "".join(lines)
    return {"results": results, "narrative": text, "evidence_card": _card("trace", text, basis, kv=kv)}


def mc_hop_key(mc, table):
    """主线中某一跳声明的键列(供下一跳取值)。"""
    return next((h.get("key") for h in mc.get("hops") or [] if h.get("table") == table), None)


# ---- 推演:参数接地
def _date(y, m, d):
    try:
        return dt.date(y, m, d)
    except ValueError:
        return None


def _date_slot(question, today):
    q = question or ""
    m = re.search(r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})", q)
    if m:
        d = _date(int(m[1]), int(m[2]), int(m[3]))
        return d.isoformat() if d else None
    m = re.search(r"(\d{1,2})月(\d{1,2})[日号]", q)
    if m:
        d = _date(today.year, int(m[1]), int(m[2]))
        if not d:
            return None
        if d < today:
            d = _date(today.year + 1, d.month, d.day)
        return d.isoformat() if d else None
    m = re.search(r"([0-9]+|[一二两三四五六七八九十半]{1,3})\s*(个月|周|天)(以?后|之后)", q)
    if m:
        n = _num(m[1])
        if n:
            return (today + dt.timedelta(days=int(n * (30 if m[2] == "个月" else 7 if m[2] == "周" else 1)))).isoformat()
    if "下个月底" in q or "下月底" in q:
        return _month_range(today, 1)[1].isoformat()
    if "年底" in q:
        return dt.date(today.year, 12, 31).isoformat()
    return None


def ground_param(name, spec, question, connect, today):
    """返回 (取值, 说明, 候选);取不到时取值为 None。"""
    if (spec or {}).get("type") == "date":
        v = _date_slot(question, today)
        return v, (f"由问句得到日期 {v}" if v else ""), []
    t, k = spec.get("table"), spec.get("key")
    match = [c for c in spec.get("match") or [k] if _ident(c)]
    if not (t and k and _ident(t) and _ident(k) and match and _has_table(connect, t)):
        return None, "", []
    rows = _rows(connect, f'SELECT {", ".join(chr(34) + c + chr(34) for c in dict.fromkeys([k] + match))} FROM "{t}"', limit=20000)
    q = question or ""
    exact = [(len(str(r.get(c) or "")), r, str(r.get(c))) for r in rows for c in match
             if len(str(r.get(c) or "")) >= 2 and _contains(q, str(r.get(c)))]
    if exact:
        _, r, v = max(exact, key=lambda x: x[0])
        return r[k], f"问句中的「{v}」→ {t}.{k} = {r[k]}", []
    via = spec.get("via") or {}
    if all(_ident(via.get(x)) for x in ("table", "key", "column")) and _has_table(connect, via["table"]):
        for tok in dict.fromkeys(x.strip("./-_") for x in _CODE.findall(q)):
            got = _rows(connect, f'SELECT "{via["column"]}" AS v FROM "{via["table"]}" WHERE "{via["key"]}" = ? LIMIT 1', (tok,), 1)
            if got and got[0]["v"]:
                return got[0]["v"], f"{via['table']} {tok} 的 {via['column']} = {got[0]['v']}", []
    specs = sorted({x for x in re.findall(r"[A-Za-z0-9][A-Za-z0-9/._-]{3,}", q) if re.search(r"[A-Za-z]", x) and re.search(r"\d", x)},
                   key=len, reverse=True)
    for tok in specs:                 # 规格类片段(如 63MVA/110kV)包含在取值里:多条时取编号最小的一条并说明
        same = sorted((r for r in rows if any(tok in str(r.get(c) or "") for c in match)), key=lambda r: str(r[k]))
        if same:
            return same[0][k], f"问句中的「{tok}」匹配到 {len(same)} 条 {t},取 {same[0][k]}", []
    label = spec.get("label") or (match[1] if len(match) > 1 else k)
    return None, "", [f"{r[k]}({r.get(label, '')})" for r in rows[:5]]


def run_whatif(route, question, ir, connect, invoke, today, operator, actions):
    act = route["action"]
    pg = (ir or {}).get("param_grounding") or {}
    params, notes, missing = {}, [], []
    for p in act.get("params") or []:
        spec = pg.get(p["name"])
        v, note, cands = ground_param(p["name"], spec, question, connect, today) if spec else (None, "", [])
        if v is not None:
            params[p["name"]] = v
            if note:
                notes.append(note)
        elif p.get("required"):
            missing.append((p, cands))
    if missing:
        ask = ";".join(f"{p['cn']}" + (f"(例如 {'、'.join(c)})" if c else "") for p, c in missing)
        text = f"要用动作「{act['cn']}」推演,还需要:{ask}。请在问句里补上后再问。"
        card = _card("clarify", text, [{"kind": "action", "cn": act["cn"], "text": act.get("desc"), "params": params}],
                     kv=list(params.items()))
        return {"results": [], "narrative": text, "evidence_card": card}
    if act.get("risk") == "high":
        text = f"「{act['cn']}」是高风险动作,问数不直接发起;参数已从问句取得,请到动作中心发起并走审批。"
        card = _card("whatif", text, [{"kind": "action", "cn": act["cn"], "text": act.get("desc"), "params": params, "notes": notes}],
                     kv=list(params.items()), next_=[{"kind": "action", "label": f"发起「{act['cn']}」(需审批)", "action_id": act["id"],
                                                     "href": "#actioncenter"}])
        return {"results": [], "narrative": text, "evidence_card": card}
    code, body = invoke(act["id"], params, operator)
    body = body or {}
    res = body.get("result") or {}
    base = {"kind": "action", "cn": act["cn"], "params": params, "notes": notes, "record_id": body.get("id")}
    center = [{"kind": "action", "label": f"到动作中心发起「{act['cn']}」", "action_id": act["id"], "href": "#actioncenter"}]
    if body.get("code") in ("simulate_not_allowed", "no_executor"):
        # 问数只能调用标了「只做试算」的执行器(DR-125):不满足时什么都没有调用,也没有留下动作记录
        text = (f"动作「{act['cn']}」没有配置执行器,无法试算。" if body["code"] == "no_executor"
                else f"动作「{act['cn']}」的执行器没有标记为只做试算,问数不调用它。参数已从问句取得,可到动作中心发起。")
        return {"results": [], "narrative": text, "evidence_card": _card("whatif", text, [base], kv=list(params.items()), next_=center)}
    if body.get("status") == "unknown":
        text = (f"已向动作「{act['cn']}」的执行器发出试算请求(记录 {body.get('id')}),但没有拿到完整的回执,结果未知。"
                "请到动作中心查看这条记录,不要据此下结论。")
        return {"results": [], "narrative": text, "evidence_card": _card("whatif", text, [base], kv=list(params.items()),
                                                                           links=[{"label": "动作记录", "href": "#actioncenter"}])}
    if code >= 400 or body.get("status") == "failed" or res.get("ok") is False:
        why = body.get("error") or res.get("summary") or res.get("error") or "执行失败"
        text = f"动作「{act['cn']}」执行失败:{why}"
        return {"results": [], "narrative": text, "evidence_card": _card("whatif", text, [base])}
    if body.get("execution_mode") not in ("executor", "simulate"):
        text = f"动作「{act['cn']}」已登记(编号 {body.get('id')}),但没有配置执行器,未做推演。"
        return {"results": [], "narrative": text, "evidence_card": _card("whatif", text, [base])}
    detail: dict = res["trial"] if isinstance(res.get("trial"), dict) else res
    table = next((v for v in (detail.get("plan"), res.get("schemes"), res.get("tasks"), detail.get("tasks"))
                  if isinstance(v, list) and v and isinstance(v[0], dict)), [])
    kv = [(EXECUTOR_FIELDS[k], detail.get(k)) for k in ("promise", "earliest_finish", "late_days") if detail.get(k) is not None]
    if res.get("recommended"):
        kv.append((EXECUTOR_FIELDS["recommended"], res["recommended"]))
    others = [a for a in bound_actions(actions, ir) if a["id"] != act["id"] and a.get("object") == act.get("object") and a.get("risk") != "high"]
    nxt = [{"kind": "question", "label": f"用「{a['cn']}」", "q": f"{question}(用「{a['cn']}」)"} for a in others[:2]]
    text = (res.get("summary") or f"动作「{act['cn']}」已执行") + (f"\n\n参数:{';'.join(notes)}" if notes else "")
    card = _card("whatif", res.get("summary") or act["cn"],
                 [{**base, "path": detail.get("decision_path") or [], "validation": res.get("validation"),
                   "real_writeback": body.get("real_writeback")}],
                 kv=kv, next_=nxt, links=[{"label": "动作记录", "href": "#actioncenter"}])
    return {"results": [_result(act["cn"], "", table)] if table else [], "narrative": text, "evidence_card": card}


# ------------------------------------------------------------------ 入口
def answer(question, ir, rules, actions, route, connect, anchored, validate=None, invoke=None, operator="问数助手"):
    """非查数类型的完整答案;返回 None 表示交回 NL2SQL。validate(sql) -> (ok, why) 为本体约束校验(可选)。"""
    t = route["type"]
    rules = rules_on_tables(rules, ir)
    if route.get("rule"):
        route = dict(route, rule=rules_on_tables([route["rule"]], ir)[0])
    today = _clock(ir, connect)[0]
    if t == "unlit":
        return run_unlit(route, question)
    if t == "judge":
        return run_judge(route, connect, actions, ir, validate)
    if t == "monitor":
        return run_monitor(rules, connect, actions, ir)
    if t == "explain":
        return run_explain(question, ir, rules, anchored, tuple(route.get("pinned") or ()))
    if t == "trace":
        return run_trace(route, ir, rules, connect, today, validate)
    if t == "whatif":
        if invoke is None:                    # 调用方没有试算的权限(如经接口代理进来的智能体):说明原因,不调用任何东西
            act = route["action"]
            text = f"「{act.get('cn')}」这类试算需要使用者在 DataMind 界面里发起,当前调用方不能触发。"
            return {"results": [], "narrative": text, "evidence_card": _card("whatif", text, [
                {"kind": "action", "cn": act.get("cn"), "text": act.get("desc"), "params": {}}])}
        return run_whatif(route, question, ir, connect, invoke, today, operator, actions)
    return None
