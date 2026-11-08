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

MAX_Q = 400
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
 "themes":[{{"name":"分析主题名(英文)","aliases":["常见说法"],"definition":"这个主题回答什么问题、看哪些量","table":"名称-取值型表","name_col":"名称列","codes":["与主题相关的全部代码,5~20 个"]}}],
 "caveats":[{{"statement":"一句话说明数据里会让查询出错的情况","check_sql":"一条只读 SELECT,用来证实这个情况(返回计数或样例)"}}]}}
要求:表名、列名、取值必须来自下面的库结构;不要编造;派生指标 5~15 个(比率、增长率等问题里常要计算的量),使用提醒 3~10 条。
先把这批问题涉及的主题逐一列出(包括较少出现的主题),每个主题至少对应一个概念,概念数量不设上限;
另外按问题的分析主题(如风险、融资、资本结构这一类)给出 themes:每个主题把名称-取值型表里所有相关代码列全;
主题在库里由几种代码共同记录时,把这些代码都列进同一个概念的 filter。
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


REJECTED: list = []          # 未通过核验的提议与原因(构建日志与调试用)


def _reject(c: dict, why: str):
    REJECTED.append({"name": str(c.get("name") or c.get("statement") or "")[:80], "why": why,
                     "table": c.get("table"), "column": c.get("column") or c.get("name_col"),
                     "filter": str(c.get("filter") or c.get("check_sql") or "")[:200], "_raw": c})
    return None


FIXABLE = ("表不存在", "列不存在", "名称列不存在", "筛选条件命中 0 行", "筛选条件无法执行")
REPAIR_ROUNDS = 2

REPAIR_PROMPT = """上一轮提议的下列概念没有通过数据核验(原因见每条)。请对照下面给出的真实表结构与取值画像,
以及已经由数据核验成立的"问题用语 → 列"对应,重新提议这些概念:表名、列名、取值必须取自下面的内容;
确实找不到对应数据的概念就不要再提。只输出一个 JSON:{{"concepts":[同上一轮的概念格式]}}

未通过的概念:
{failed}

相关表的真实结构与取值画像(〔〕内为由数据推出的候选名):
{tables}

已核验的问题用语 → 列:
{groundings}
"""


def _related_tables(failed: list, groundings: list, prof: dict, k: int = 8) -> list:
    """repair context: the proposed tables plus tables whose grounded phrases share words with the failed concepts."""
    out = [str(f.get("table") or "") for f in failed if str(f.get("table") or "") in prof]
    words = {w for f in failed for w in re.findall(r"[a-z]{4,}", re.sub(r"([a-z])([A-Z])", r"\1 \2", str(f.get("name") or "")).lower())}
    for g in groundings:
        if words & set(re.findall(r"[a-z]{4,}", g["term"].lower())):
            out.append(g["table"])
    return list(dict.fromkeys(out))[:k]


def verify_concept(con: sqlite3.Connection, c: dict, tables: dict, entity_col: str | None = None) -> dict | None:
    t, col = str(c.get("table") or ""), str(c.get("column") or "")
    real_t = tables.get(t.lower())
    if not real_t:
        return _reject(c, "表不存在")
    cols = {r[1].lower(): r[1] for r in con.execute(f"PRAGMA table_info({_q(real_t)})")}
    if col and col.lower() not in cols:
        return _reject(c, "列不存在")
    flt = str(c.get("filter") or "").strip()
    try:
        n = _run(con, f"SELECT COUNT(*) FROM {_q(real_t)}" + (f" WHERE {flt}" if flt else ""))[0][0]
    except (sqlite3.Error, ValueError) as e:
        return _reject(c, f"筛选条件无法执行:{str(e)[:60]}")
    if not n:
        return _reject(c, "筛选条件命中 0 行")
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


def verify_theme(con: sqlite3.Connection, th: dict, tables: dict, entity_of: dict) -> dict | None:
    """主题:名称列里每个代码都要在库中存在;存在的不少于 2 个才成立。"""
    real_t = tables.get(str(th.get("table") or "").lower())
    if not real_t:
        return _reject(th, "表不存在")
    cols = {r[1].lower(): r[1] for r in con.execute(f"PRAGMA table_info({_q(real_t)})")}
    nc = cols.get(str(th.get("name_col") or "").lower())
    if not nc:
        return _reject(th, "名称列不存在")
    ent = entity_of.get(real_t.lower())
    kept = []
    for code in [str(x) for x in th.get("codes") or []][:40]:
        try:
            row = _run(con, f"SELECT COUNT(*)" + (f", COUNT(DISTINCT {_q(cols[ent.lower()])})" if ent and ent.lower() in cols else "")
                       + f" FROM {_q(real_t)} WHERE {_q(nc)} = ?".replace("?", "'" + code.replace("'", "''") + "'"))[0]
        except (sqlite3.Error, ValueError):
            continue
        if row[0]:
            kept.append({"code": code, "rows": row[0], "entities": row[1] if len(row) > 1 else None})
    if len(kept) < 2:
        return _reject(th, "相关代码在库中不足 2 个")
    return {"name": str(th.get("name") or "")[:80], "definition": str(th.get("definition") or "")[:300],
            "aliases": [str(a)[:60] for a in (th.get("aliases") or []) if str(a).strip()][:8],
            "table": real_t, "name_col": nc, "codes": kept}


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
          extract: Callable[[str], dict | None], profile: dict | None = None, labels: dict | None = None,
          groundings: list | None = None) -> dict:
    """返回 {"concepts": [...], "caveats": [...], "proposed": n, "dropped": m}。"""
    REJECTED.clear()
    qs = [str(q).strip() for q in questions if str(q).strip()][:MAX_Q]
    if not qs:
        return {"concepts": [], "caveats": [], "proposed": 0, "dropped": 0}
    tables = {r[0].lower(): r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if profile:                                       # DR-093:有取值画像时,结构摘要带类型、去重数、常见取值与候选名
        import grounding as _g
        brief = _g.profile_brief(profile, labels)
        if groundings:
            brief += "\n\n已由数据核验的问题用语 → 列(可直接采用):\n" + "\n".join(
                f"- '{g['term']}' -> {g['table']}.{g['column']}{' WHERE ' + g['filter'] if g['filter'] else ''}"
                for g in groundings[:300])
    else:
        brief = schema_brief(con, ir, docs)
    groundings = groundings or []
    repaired = {"rounds": 0, "resubmitted": 0, "recovered": 0}
    concepts, caveats, seen, seen_map, proposed = [], [], set(), set(), 0
    pending_derived: list = []
    themes: list = []
    entity_of = {o["table"].lower(): (o.get("catalog") or {}).get("entity_col")
                 for o in ir.get("objects") or [] if o.get("table") and (o.get("catalog") or {}).get("entity_col")}
    for i in range(0, len(qs), BATCH):
        reply = ask(PROMPT.format(schema=brief[:(90000 if profile else 24000)], questions="\n".join(f"- {q[:300]}" for q in qs[i:i + BATCH])))
        d = extract(reply or "") or {}
        todo = [c for c in d.get("concepts") or []]
        for rnd in range(REPAIR_ROUNDS + 1):              # DR-093:未通过核验的概念带着原因与真实结构重提,至多 2 轮
            before = len(REJECTED)
            for c in todo:
                proposed += 1 if rnd == 0 else 0
                ent = entity_of.get(str((c or {}).get("table") or "").lower()) if isinstance(c, dict) else None
                v = verify_concept(con, c, tables, ent) if isinstance(c, dict) else None
                sig = (v["table"], v["column"], re.sub(r"\s+", " ", v["filter"]).lower()) if v else None
                if v and v["name"] and v["name"].lower() not in seen and sig not in seen_map:   # 名称或取数方式重复的不再收
                    seen.add(v["name"].lower())
                    seen_map.add(sig)
                    if rnd:
                        v["repaired_round"] = rnd
                        repaired["recovered"] += 1
                    concepts.append(v)
            failed = [r for r in REJECTED[before:] if r["why"].startswith(FIXABLE) and isinstance(r.get("_raw"), dict)
                      and "codes" not in r["_raw"]]
            if not profile or rnd == REPAIR_ROUNDS or not failed:
                break
            rel = _related_tables(failed, groundings, profile)
            fl = "\n".join(f"- {f['name']}: 提议 {f.get('table')}.{f.get('column') or ''}"
                           + (f" WHERE {f['filter']}" if f.get('filter') else "") + f" → {f['why']}" for f in failed[:40])
            gl = "\n".join(f"- '{g['term']}' -> {g['table']}.{g['column']}{' WHERE ' + g['filter'] if g['filter'] else ''}"
                           for g in groundings if g["table"] in rel)[:8000]
            reply = ask(REPAIR_PROMPT.format(failed=fl, tables=_g.profile_brief(profile, labels, rel), groundings=gl or "(无)"))
            todo = [c for c in (extract(reply or "") or {}).get("concepts") or [] if isinstance(c, dict)]
            repaired["rounds"] += 1
            repaired["resubmitted"] += len(todo)
            for f in failed:
                f["repair_round"] = rnd + 1
        pending_derived += [c for c in d.get("derived") or [] if isinstance(c, dict)]
        for th in d.get("themes") or []:               # 主题:代码逐个在库中核验,存在的才保留,并记录覆盖实体数
            if not isinstance(th, dict):
                continue
            proposed += 1
            v = verify_theme(con, th, tables, entity_of)
            if v and v["name"].lower() not in {x["name"].lower() for x in themes}:
                themes.append(v)
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
    kept_names = {c["name"].lower() for c in concepts}
    rejected = [dict({k: v for k, v in r.items() if k != "_raw"}, recovered=True) if str(r.get("name") or "").lower() in kept_names
                else {k: v for k, v in r.items() if k != "_raw"} for r in REJECTED]
    return {"concepts": concepts, "derived": derived, "themes": themes, "caveats": caveats, "proposed": proposed, "rejected": rejected,
            "repair": repaired,
            "dropped": proposed - len(concepts) - len(caveats) - len(derived) - len(themes)}


# ── DR-092 按失败项补本体:模型提议,数据裁决 ───────────────────────────────────

PATCH_PROMPT = """下面是数据分析智能体在一批分析任务上漏答或答错的检查项(问题、期望答案要点、评审说明),
以及与这些项相关、在库中实际存在的代码(名称-取值型表的名称列,括号内为覆盖的实体数)。
当前语义层已有的主题与概念名:{existing}

请提议对语义层的补充,让智能体下次能找到回答这些问题所需的数据。只输出一个 JSON:
{{"themes":[{{"name":"分析主题名(英文)","aliases":["常见说法"],"definition":"这个主题回答什么问题、看哪些量","table":"{table}","name_col":"{name_col}","codes":["相关代码,只能从下面列出的代码中选"]}}],
 "guidance":[{{"statement":"一句话的取数建议或注意事项","check_sql":"一条只读 SELECT,证实这条建议所依据的数据情况"}}]}}
要求:不要重复已有主题;代码只能取自下面列出的候选;没有合适数据的问题不要硬凑;
只提最能覆盖这批失败项的主题,至多 3 个;取数建议至多 2 条。

失败项:
{failures}

候选代码:
{codes}
"""

_STOP = {"what", "which", "how", "does", "company", "companies", "their", "about", "trend", "trends", "recent", "during",
         "period", "major", "main", "change", "changes", "changed", "affect", "affected", "should", "there", "these", "those",
         "with", "from", "into", "over", "years", "year", "most", "were", "have", "been", "being", "that", "this"}


def code_candidates(con: sqlite3.Connection, table: str, name_col: str, entity_col: str | None, texts: list,
                    per_word: int = 6, cap: int = 160) -> list:
    """按失败项里的关键词在名称列中检索实际存在的代码,按覆盖实体数排序。"""
    words = []
    for t in texts:
        for w in re.findall(r"[A-Za-z]{5,}", t or ""):
            w = w.lower()
            if w not in _STOP and w not in words:
                words.append(w)
    out, seen = [], set()
    ent = f"COUNT(DISTINCT {_q(entity_col)})" if entity_col else "COUNT(*)"
    for w in words[:60]:
        stem = w[:7]
        try:
            rows = _run(con, f"SELECT {_q(name_col)}, {ent} FROM {_q(table)} WHERE {_q(name_col)} LIKE ? "
                             f"GROUP BY 1 ORDER BY 2 DESC LIMIT {per_word}".replace("?", f"'%{stem}%'"), limit=per_word)
        except (sqlite3.Error, ValueError):
            continue
        for code, n in rows:
            if code not in seen:
                seen.add(code)
                out.append((str(code), n))
        if len(out) >= cap:
            break
    return out


def propose_from_failures(con: sqlite3.Connection, ir: dict, failures: list, ask: Callable[[str], str],
                          extract: Callable[[str], dict | None], batch: int = 25,
                          max_themes: int = 15, max_guidance: int = 10) -> dict:
    """failures: [{question, expected, reason}]。返回经数据核验的 {"themes": [...], "caveats": [...], "proposed": n}。"""
    REJECTED.clear()
    cat_obj = next((o for o in ir.get("objects") or [] if (o.get("catalog") or {}).get("name_col")), None)
    if not cat_obj or not failures:
        return {"themes": [], "caveats": [], "proposed": 0, "rejected": []}
    cat = cat_obj["catalog"]
    table, nc, ent = cat_obj["table"], cat["name_col"], cat.get("entity_col")
    tables = {r[0].lower(): r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    existing = sorted({str(x.get("name")) for k in ("themes", "concepts") for x in ir.get(k) or []})
    themes, caveats, proposed = [], [], 0
    for i in range(0, len(failures), batch):
        part = failures[i:i + batch]
        codes = code_candidates(con, table, nc, ent, [f"{f['question']} {f.get('expected', '')}" for f in part])
        if not codes:
            continue
        reply = ask(PATCH_PROMPT.format(
            existing=", ".join(existing)[:3000], table=table, name_col=nc,
            failures="\n".join(f"- 问题:{f['question'][:200]}\n  期望要点:{str(f.get('expected', ''))[:300]}\n  评审:{str(f.get('reason', ''))[:200]}" for f in part),
            codes=", ".join(f"{c}({n})" for c, n in codes)))
        d = extract(reply or "") or {}
        allowed = {c for c, _ in codes}
        for th in d.get("themes") or []:
            if not isinstance(th, dict):
                continue
            proposed += 1
            th = dict(th, table=table, name_col=nc, codes=[c for c in th.get("codes") or [] if str(c) in allowed])
            v = verify_theme(con, th, tables, {table.lower(): ent} if ent else {})
            if v and v["name"].lower() not in {x["name"].lower() for x in themes} and v["name"] not in existing:
                themes.append(v)
        for g in d.get("guidance") or []:
            if not isinstance(g, dict):
                continue
            proposed += 1
            v = verify_caveat(con, g)
            if v:
                caveats.append(v)
    if max_themes:                                    # 补丁限量:按覆盖实体数保留前 max_themes 个主题
        themes = sorted(themes, key=lambda t: -max([k.get("entities") or 0 for k in t["codes"]] or [0]))[:max_themes]
    return {"themes": themes, "caveats": caveats[:max_guidance] if max_guidance else caveats,
            "proposed": proposed, "rejected": list(REJECTED)}
