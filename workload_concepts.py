"""按业务问题样本补充概念层(DR-088):模型提议,数据裁决。

输入是构建时随附的一批业务问题(只有问题,没有答案)。模型根据问题、库结构、字段说明与取值目录提议:
- 概念:问题里反复出现的业务概念对应到哪张表、哪一列、什么筛选条件、怎样汇总;
- 使用提醒:数据里会让查询出错的情况(重复记录、字段含义与名称不符、取值格式等),每条带一条验证查询。

提议一律在只读库上核验,不通过的丢弃:
- 概念:表与列存在,筛选条件可执行且至少命中一行;记录命中行数;
- 使用提醒:验证查询可执行且返回非空、非零的结果;记录查询与结果。
验收问题(cqs)仍然留出,不进入这里。
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from typing import Callable

MAX_Q = 120
BATCH = 40


def _q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def schema_brief(con: sqlite3.Connection, ir: dict, docs: dict, limit_cols: int = 40) -> str:
    """给模型看的库结构:表、列(带说明)、名称-取值型表的常见取值。"""
    lines = []
    for o in ir.get("objects") or []:
        t = o.get("table")
        if not t or o.get("kind") == "action":
            continue
        try:
            cols = [(r[1], r[2]) for r in con.execute(f"PRAGMA table_info({_q(t)})")]
        except sqlite3.Error:
            continue
        d = docs.get(t.lower()) or {}
        parts = [c + (f"[{d[c.lower()][:80]}]" if d.get(c.lower()) else "") for c, _ in cols[:limit_cols]]
        lines.append(f"- {t}: " + ", ".join(parts))
        cat = o.get("catalog") or {}
        if cat.get("entries"):
            ent = f"(按覆盖的 {cat['entity_col']} 数)" if cat.get("entity_col") else ""
            lines.append(f"  {cat['name_col']} 常见取值{ent}: "
                         + ", ".join(f"{e['value']}({e['entities']})" if cat.get("entity_col") else e["value"] for e in cat["entries"]))
    return "\n".join(lines)


PROMPT = """你在为一个数据库构建语义层。下面是这个库的结构,以及一批真实的业务问题(只有问题)。
请找出这些问题反复用到的业务概念与需要注意的数据情况,只输出一个 JSON:
{{"concepts":[{{"name":"概念名(英文)","aliases":["问题里对它的常见说法,3~6 个"],"definition":"一句话定义","table":"表名","column":"数值或属性所在列",
  "filter":"SQL WHERE 条件片段,没有就留空","aggregation":"怎样汇总(如 SUM / latest value per entity)"}}],
 "derived":[{{"name":"派生指标名(英文)","aliases":["常见说法,3~6 个"],"definition":"一句话定义","formula":"用上面概念名写的计算式,如 GrossProfit / Revenue","uses":["用到的概念名"]}}],
 "caveats":[{{"statement":"一句话说明数据里会让查询出错的情况","check_sql":"一条只读 SELECT,用来证实这个情况(返回计数或样例)"}}]}}
要求:表名、列名、取值必须来自下面的库结构;不要编造;概念 15~40 个,派生指标 5~15 个(比率、增长率等问题里常要计算的量),使用提醒 3~10 条。
同一个业务概念在名称列里可能有几种写法(不同实体用不同的代码记录同一件事):把它们都列进 filter,
写成「名称列 IN ('代码1','代码2',...)」,让概念覆盖尽量多的实体;不同概念不要重复。

库结构:
{schema}

业务问题:
{questions}
"""


def _run(con: sqlite3.Connection, sql: str, timeout: float = 15.0, limit: int = 5):
    if not re.match(r"^\s*(select|with)\b", sql or "", re.I) or ";" in sql.strip().rstrip(";"):
        raise ValueError("只接受单条只读查询")
    deadline = time.time() + timeout
    con.set_progress_handler(lambda: 1 if time.time() > deadline else 0, 10000)
    try:
        return con.execute(sql.strip().rstrip(";")).fetchmany(limit)
    finally:
        con.set_progress_handler(None, 0)


def verify_concept(con: sqlite3.Connection, c: dict, tables: dict, entity_col: str | None = None) -> dict | None:
    t, col = str(c.get("table") or ""), str(c.get("column") or "")
    real_t = tables.get(t.lower())
    if not real_t:
        return None
    cols = {r[1].lower(): r[1] for r in con.execute(f"PRAGMA table_info({_q(real_t)})")}
    if col and col.lower() not in cols:
        return None
    flt = str(c.get("filter") or "").strip()
    try:
        n = _run(con, f"SELECT COUNT(*) FROM {_q(real_t)}" + (f" WHERE {flt}" if flt else ""))[0][0]
    except (sqlite3.Error, ValueError):
        return None
    if not n:
        return None
    ev = {"rows": n}
    if entity_col and entity_col.lower() in cols:       # 覆盖的实体数:同一概念的几种代码合起来覆盖多少实体
        try:
            ev["entities"] = _run(con, f"SELECT COUNT(DISTINCT {_q(cols[entity_col.lower()])}) FROM {_q(real_t)}"
                                       + (f" WHERE {flt}" if flt else ""))[0][0]
        except (sqlite3.Error, ValueError):
            pass
    return {"name": str(c.get("name") or "")[:80], "definition": str(c.get("definition") or "")[:300],
            "aliases": [str(a)[:60] for a in (c.get("aliases") or []) if str(a).strip()][:8],
            "table": real_t, "column": cols.get(col.lower(), "") if col else "", "filter": flt[:600],
            "aggregation": str(c.get("aggregation") or "")[:120], "evidence": ev}


def verify_caveat(con: sqlite3.Connection, c: dict) -> dict | None:
    sql = str(c.get("check_sql") or "")
    try:
        rows = _run(con, sql)
    except (sqlite3.Error, ValueError):
        return None
    if not rows or all(v in (None, 0, "", 0.0) for v in rows[0]):
        return None
    return {"statement": str(c.get("statement") or "")[:300], "check_sql": sql[:600],
            "evidence": {"result": json.dumps(rows[:3], ensure_ascii=False, default=str)[:300]}}


def build(con: sqlite3.Connection, ir: dict, docs: dict, questions: list, ask: Callable[[str], str],
          extract: Callable[[str], dict | None]) -> dict:
    """返回 {"concepts": [...], "caveats": [...], "proposed": n, "dropped": m}。"""
    qs = [str(q).strip() for q in questions if str(q).strip()][:MAX_Q]
    if not qs:
        return {"concepts": [], "caveats": [], "proposed": 0, "dropped": 0}
    tables = {r[0].lower(): r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    brief = schema_brief(con, ir, docs)
    concepts, caveats, seen, seen_map, proposed = [], [], set(), set(), 0
    pending_derived: list = []
    entity_of = {o["table"].lower(): (o.get("catalog") or {}).get("entity_col")
                 for o in ir.get("objects") or [] if o.get("table") and (o.get("catalog") or {}).get("entity_col")}
    for i in range(0, len(qs), BATCH):
        reply = ask(PROMPT.format(schema=brief[:24000], questions="\n".join(f"- {q[:300]}" for q in qs[i:i + BATCH])))
        d = extract(reply or "") or {}
        for c in d.get("concepts") or []:
            proposed += 1
            ent = entity_of.get(str((c or {}).get("table") or "").lower()) if isinstance(c, dict) else None
            v = verify_concept(con, c, tables, ent) if isinstance(c, dict) else None
            sig = (v["table"], v["column"], re.sub(r"\s+", " ", v["filter"]).lower()) if v else None
            if v and v["name"] and v["name"].lower() not in seen and sig not in seen_map:   # 名称或取数方式重复的不再收
                seen.add(v["name"].lower())
                seen_map.add(sig)
                concepts.append(v)
        pending_derived += [c for c in d.get("derived") or [] if isinstance(c, dict)]
        for c in d.get("caveats") or []:
            proposed += 1
            v = verify_caveat(con, c) if isinstance(c, dict) else None
            if v:
                caveats.append(v)
    derived, names = [], {c["name"].lower(): c for c in concepts}
    for c in pending_derived:                         # 派生指标:用到的概念都已通过数据核验才保留
        proposed += 1
        uses = [str(u) for u in c.get("uses") or []]
        if c.get("name") and c.get("formula") and uses and all(u.lower() in names for u in uses) \
                and str(c["name"]).lower() not in names and str(c["name"]).lower() not in {x["name"].lower() for x in derived}:
            derived.append({"name": str(c["name"])[:80], "definition": str(c.get("definition") or "")[:300],
                            "aliases": [str(a)[:60] for a in (c.get("aliases") or []) if str(a).strip()][:8],
                            "formula": str(c["formula"])[:300], "uses": uses,
                            "inputs": [{"name": u, "table": names[u.lower()]["table"], "column": names[u.lower()]["column"],
                                        "filter": names[u.lower()]["filter"]} for u in uses]})
    return {"concepts": concepts, "derived": derived, "caveats": caveats, "proposed": proposed,
            "dropped": proposed - len(concepts) - len(caveats) - len(derived)}
