#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""指标契约:可编译、可执行、可核验的指标定义 —— DR-054。

此前 ``metric_layers`` 里的指标只有名称、说明、绑定表和取值列,聚合方式在查询时
按名称正则猜(带「率」取均值,带「库存」取月均,否则求和),日期列也是现场找。指标
因此只是文本,转不成稳定 SQL,也无法与任何参照结果比对。

本模块把指标提到与关系同等的地位:

- 契约字段声明「怎么算」——绑定对象、度量列、聚合、过滤、时间列与粒度、可用维度;
- ``compile_sql`` 由契约**确定性**生成 SQL(不调模型),同一契约反复编译逐字节一致;
- ``adjudicate`` 只读执行编译结果,并与参照(历史 SQL / 参考基准 / 沉淀 SQL)比对;
  ``verified`` 只来自「可执行 ∧ 与参照一致」,与关系的裁决原则同源;
- ``audit`` 检查 ``verified`` 指标是否携带可回放证据,供验收检查消费。

状态(与关系的 verified/asserted/candidate 对称):
  candidate   提议或反解得到,尚未通过执行核验;可执行但无参照者仍是 candidate
  verified    编译结果只读执行成功,且与参照结果一致
  certified   人工确认口径(等价于关系的 asserted);只能由人授予,本模块不会自动升
  deprecated  人工停用;保留记录,不再被默认消费

旧形状(只有 table/value_col)的指标仍可读取:``normalize`` 会把它归一成契约,但没有
聚合声明的指标不会被编译,也不会被标成任何比 candidate 更高的状态。
"""
from __future__ import annotations

import re
import sqlite3
import time

AGGS = ("sum", "count", "count_distinct", "avg", "min", "max")
FILTER_OPS = ("=", "!=", ">", ">=", "<", "<=", "in", "not_in", "is_null", "not_null")
GRAINS = ("day", "month", "quarter", "year")
LAYERS = ("atomic", "derived", "composite")
STATUSES = ("candidate", "verified", "certified", "deprecated")
STATUS_RANK = {"deprecated": -1, "candidate": 0, "verified": 1, "certified": 2}
DEFAULT_TOLERANCE = 0.01          # 相对容差 1%;参照为 0 时退化为绝对容差
QUERY_TIMEOUT_S = 8.0             # 与深度问数的语句级超时一致

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_STRONG = ("verified", "asserted")


def _ident(name):
    """标识符白名单:表名/列名要拼进 SQL 标识符位,不能靠「来自 IR」这层假设。"""
    s = str(name or "").strip()
    return s if _IDENT.match(s) else ""


def _q(name):
    return '"' + str(name).replace('"', '""') + '"'


def _alias(name):
    """结果列别名可以是中文;成对转义引号即可,不能越出引号边界。"""
    return '"' + str(name).replace('"', '""') + '"'


def is_contract(m):
    return isinstance(m, dict) and isinstance(m.get("measure"), dict) and bool(m["measure"].get("agg"))


def status_rank(status):
    return STATUS_RANK.get(str(status or "candidate"), 0)


def normalize(m, ir=None):
    """归一为契约。结构非法(标识符/聚合/算子不合法)时抛 ValueError——带病入库会污染下游。

    旧形状指标(无 measure)也归一,保留 table/value_col 供既有消费方使用,
    但 measure.agg 为空,表示「尚无可编译口径」。
    """
    if not isinstance(m, dict):
        raise ValueError("指标须为对象")
    name = str(m.get("name") or "").strip()
    if not name:
        raise ValueError("指标 name 必填")
    out = {
        "id": str(m.get("id") or name).strip()[:80],
        "name": name[:80],
        "synonyms": _clean_list(m.get("synonyms") or m.get("aliases"), 20, 40),
        "layer": m.get("layer") if m.get("layer") in LAYERS else "atomic",
        "type": str(m.get("type") or "")[:40],
        "desc": str(m.get("desc") or m.get("description") or "")[:400],
        "unit": str(m.get("unit") or "")[:20],
        "entity": str(m.get("entity") or "").strip()[:80],
        "status": m.get("status") if m.get("status") in STATUSES else "candidate",
        "provenance": list(m.get("provenance") or [])[:20] if isinstance(m.get("provenance"), list)
                      else ([m["provenance"]] if isinstance(m.get("provenance"), dict) else []),
        "evidence": dict(m.get("evidence") or {}) if isinstance(m.get("evidence"), dict) else {},
        "candidate": bool(m.get("candidate", True)),
    }
    measure = m.get("measure") if isinstance(m.get("measure"), dict) else {}
    col = _ident(measure.get("col") or m.get("value_col"))
    agg = str(measure.get("agg") or "").strip().lower()
    if agg and agg not in AGGS:
        raise ValueError(f"聚合方式不合法:{agg}(允许 {'/'.join(AGGS)})")
    if agg and not col and agg not in ("count",):
        raise ValueError(f"指标「{name}」声明了聚合 {agg} 但缺少合法度量列")
    out["measure"] = {"col": col, "agg": agg}
    table = _ident(m.get("table") or m.get("bound_table"))
    if not table and out["entity"] and ir:
        table = _ident(_entity_table(out["entity"], ir))
    out["table"] = table
    out["value_col"] = col                      # 旧消费方(口径卡/上下文)仍读这两个字段
    filters = []
    for f in (m.get("filters") or []):
        if not isinstance(f, dict):
            raise ValueError("filters 元素须为对象")
        fc, op = _ident(f.get("col")), str(f.get("op") or "=").strip().lower()
        if not fc:
            raise ValueError("过滤条件缺少合法列名")
        if op not in FILTER_OPS:
            raise ValueError(f"过滤算子不合法:{op}")
        val = f.get("value")
        if op in ("in", "not_in"):
            if not isinstance(val, (list, tuple)) or not val:
                raise ValueError("in/not_in 需要非空取值列表")
            val = [_scalar(v) for v in val][:50]
        elif op in ("is_null", "not_null"):
            val = None
        else:
            val = _scalar(val)
        filters.append({"col": fc, "op": op, "value": val})
    out["filters"] = filters
    tm = m.get("time") if isinstance(m.get("time"), dict) else {}
    tcol = _ident(tm.get("col") or m.get("time_col"))
    grains = [g for g in (tm.get("grain") or tm.get("grains") or []) if g in GRAINS]
    out["time"] = {"col": tcol, "grain": grains or (["month"] if tcol else [])}
    out["dimensions"] = [d for d in (_ident(x) for x in (m.get("dimensions") or [])) if d][:20]
    inputs = _clean_list(m.get("inputs"), 20, 80)
    out["inputs"] = inputs
    out["formula"] = str(m.get("formula") or "")[:400]
    for key in ("certified_by", "certified_at", "review_reason"):
        if m.get(key):
            out[key] = str(m[key])[:120]
    return out


def _scalar(v):
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return v
    if v is None:
        return None
    return str(v)[:200]


def _clean_list(values, max_items, max_len):
    out = []
    for v in (values or []) if isinstance(values, (list, tuple)) else []:
        s = str(v).strip()[:max_len]
        if s and s not in out:
            out.append(s)
        if len(out) >= max_items:
            break
    return out


def _objects(ir):
    return (ir or {}).get("objects") or []


def _obj_key(o, idx):
    return o.get("id") or o.get("name") or o.get("cn") or f"_obj{idx}"


def _obj_table(o):
    t = o.get("table")
    if not t and o.get("tables"):
        t = o["tables"][0]
    return t or ""


def _entity_table(entity, ir):
    e = str(entity).strip().lower()
    for i, o in enumerate(_objects(ir)):
        names = {str(_obj_key(o, i)).lower(), str(o.get("name") or "").lower(),
                 str(o.get("cn") or "").lower(), str(_obj_table(o)).lower()}
        if e in names:
            return _obj_table(o)
    return ""


def _rels(ir):
    if "links" in (ir or {}):
        return ir["links"], "source", "target"
    return (ir or {}).get("relations", []), "source_concept", "target_concept"


def allowed_dimensions(contract, ir):
    """指标可下钻的维度:绑定表自身的列,以及沿 verified/asserted 关系一跳可达的对象。

    维度可达性由本体约束——这正是「本体作为契约」的含义:不在已验证关系上的表不能
    作为该指标的维度,即便 SQL 语法上能 JOIN。
    """
    table = (contract.get("table") or "").lower()
    own, joined = [], []
    key_by_table = {}
    for i, o in enumerate(_objects(ir)):
        t = _obj_table(o)
        if t:
            key_by_table[t.lower()] = _obj_key(o, i)
        if t and t.lower() == table:
            own = [a.get("col") for a in (o.get("attrs") or []) if a.get("col")]
    me = key_by_table.get(table)
    if me:
        rels, sk, tk = _rels(ir)
        for r in rels:
            if r.get("status") not in _STRONG:
                continue
            ev = r.get("evidence") or {}
            ck, pk = ev.get("child_key"), ev.get("parent_key")
            if not (ck and pk):
                continue
            s, t = r.get(sk), r.get(tk)
            if s == me and t:
                joined.append({"object": t, "table": _table_of(t, ir), "child_key": ck, "parent_key": pk,
                               "status": r.get("status")})
            elif t == me and s:
                joined.append({"object": s, "table": _table_of(s, ir), "child_key": pk, "parent_key": ck,
                               "status": r.get("status"), "fan_out_risk": True})
    return {"columns": own, "objects": [j for j in joined if j["table"]]}


def _table_of(key, ir):
    for i, o in enumerate(_objects(ir)):
        if _obj_key(o, i) == key:
            return _obj_table(o)
    return ""


def _time_bucket(col, grain):
    c = _q(col)
    if grain == "day":
        return f"substr({c},1,10)"
    if grain == "month":
        return f"substr({c},1,7)"
    if grain == "year":
        return f"substr({c},1,4)"
    if grain == "quarter":
        return (f"substr({c},1,4) || '-Q' || "
                f"((CAST(substr({c},6,2) AS INTEGER)+2)/3)")
    raise ValueError(f"粒度不合法:{grain}")


def _filter_sql(f):
    c, op, v = _q(f["col"]), f["op"], f.get("value")
    if op == "is_null":
        return f"{c} IS NULL"
    if op == "not_null":
        return f"{c} IS NOT NULL"
    if op in ("in", "not_in"):
        vals = ", ".join(_lit(x) for x in v)
        return f"{c} {'IN' if op == 'in' else 'NOT IN'} ({vals})"
    return f"{c} {op} {_lit(v)}"


def _lit(v):
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


def compile_sql(contract, grain=None, dimensions=None, order=True):
    """由契约确定性生成 SQLite SQL。

    - 无 grain、无 dimensions:标量总量(用于与参照比对);
    - grain:按时间列分桶;dimensions:只接受绑定表自身列(跨表维度由问数链路经已验证
      关系 JOIN,不在此处自造 JOIN)。
    """
    c = contract
    table, col, agg = _ident(c.get("table")), _ident((c.get("measure") or {}).get("col")), \
        (c.get("measure") or {}).get("agg")
    if not table:
        raise ValueError("指标未绑定表,无法编译")
    if agg not in AGGS:
        raise ValueError("指标缺少合法聚合方式,无法编译")
    if agg == "count" and not col:
        expr = "COUNT(*)"
    elif agg == "count_distinct":
        expr = f"COUNT(DISTINCT {_q(col)})"
    else:
        expr = f"{agg.upper()}({_q(col)})"
    select, group = [], []
    if grain:
        if grain not in GRAINS:
            raise ValueError(f"粒度不合法:{grain}")
        tcol = _ident((c.get("time") or {}).get("col"))
        if not tcol:
            raise ValueError("指标未声明时间列,不能按时间分桶")
        select.append(f"{_time_bucket(tcol, grain)} AS {_alias('期间')}")
        group.append("1")
    for d in (dimensions or []):
        di = _ident(d)
        if not di:
            raise ValueError(f"维度列不合法:{d}")
        select.append(_q(di))
        group.append(str(len(select)))
    select.append(f"{expr} AS {_alias(c.get('name') or 'value')}")
    where = " AND ".join(_filter_sql(f) for f in (c.get("filters") or []))
    sql = f"SELECT {', '.join(select)} FROM {_q(table)}"
    if where:
        sql += f" WHERE {where}"
    if group:
        sql += f" GROUP BY {', '.join(group)}"
        if order:
            sql += f" ORDER BY {', '.join(group)}"
    return sql


def schema_missing(db_path, contract):
    """契约引用的表/列是否真实存在。

    必须先于执行做:SQLite 在兼容模式下会把未知的双引号标识符当字符串字面量,
    ``SUM("amt")`` 对不存在的列也能「成功」算出 0——不查目录就会给不存在的列发证据。
    """
    c = contract
    table = _ident(c.get("table"))
    if not table:
        return ["未绑定表"]
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return [f"打开数据库失败:{type(exc).__name__}"]
    try:
        names = {r[0].lower() for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        if table.lower() not in names:
            return [f"表不存在:{table}"]
        cols = {r[1].lower() for r in con.execute(f"PRAGMA table_info({_q(table)})")}
    except sqlite3.Error as exc:
        return [f"读取表结构失败:{type(exc).__name__}"]
    finally:
        con.close()
    wanted = []
    mc = (c.get("measure") or {}).get("col")
    if mc:
        wanted.append(mc)
    wanted += [f.get("col") for f in (c.get("filters") or [])]
    tc = (c.get("time") or {}).get("col")
    if tc:
        wanted.append(tc)
    wanted += list(c.get("dimensions") or [])
    return [f"列不存在:{table}.{w}" for w in wanted if w and w.lower() not in cols]


def describe(contract):
    """契约的一行口径文本(供口径卡与问数上下文):SUM(amount) WHERE status != 'cancelled' 按 order_date/month。"""
    m = contract.get("measure") or {}
    agg, col = (m.get("agg") or "").upper(), m.get("col") or "*"
    if agg == "COUNT_DISTINCT":
        expr = f"COUNT(DISTINCT {col})"
    else:
        expr = f"{agg}({col})"
    parts = [expr]
    if contract.get("filters"):
        parts.append("WHERE " + " AND ".join(_filter_sql(f) for f in contract["filters"]))
    t = contract.get("time") or {}
    if t.get("col"):
        parts.append(f"按 {t['col']}/{(t.get('grain') or ['month'])[0]}")
    return " ".join(parts)


def execute(db_path, sql, limit=2000):
    """只读执行;失败返回 {"error": ...} 而不是抛异常——裁决要把失败如实记进证据。"""
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return {"error": f"打开数据库失败:{type(exc).__name__}"}
    deadline = time.time() + QUERY_TIMEOUT_S
    con.set_progress_handler(lambda: 1 if time.time() > deadline else 0, 10000)
    try:
        cur = con.execute(sql)
        cols = [d[0] for d in (cur.description or [])]
        rows = [list(r) for r in cur.fetchmany(limit)]
        return {"columns": cols, "rows": rows}
    except sqlite3.Error as exc:
        return {"error": f"{type(exc).__name__}: {str(exc)[:160]}"}
    finally:
        con.close()


def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def _close(a, b, tol):
    if a is None or b is None:
        return False
    if b == 0:
        return abs(a) <= tol
    return abs(a - b) <= tol * max(abs(a), abs(b))


def compare_results(mine, ref, tol=DEFAULT_TOLERANCE):
    """比较两份结果:标量对标量;或按整行(维度值 + 数值)集合比较。

    返回 (是否一致, 说明)。形状不同(行数/列数)即不一致——不做「取第一列求和」之类的
    宽松折算,那会把口径不同的两个指标判成相同。
    """
    if not mine.get("rows") or not ref.get("rows"):
        return False, "一方无结果"
    mr, rr = mine["rows"], ref["rows"]
    if len(mr) != len(rr) or len(mr[0]) != len(rr[0]):
        return False, f"形状不同:{len(mr)}×{len(mr[0])} vs {len(rr)}×{len(rr[0])}"
    def canon(row):
        out = []
        for v in row:
            n = _num(v)
            out.append(("n", round(n, 6)) if n is not None else ("s", str(v)))
        return tuple(out)
    a = sorted(canon(r) for r in mr)
    b = sorted(canon(r) for r in rr)
    for x, y in zip(a, b):
        for (kx, vx), (ky, vy) in zip(x, y):
            if kx != ky:
                return False, "取值类型不同"
            if kx == "n":
                if not _close(vx, vy, tol):
                    return False, f"数值不一致:{vx} vs {vy}"
            elif vx != vy:
                return False, f"维度值不一致:{vx} vs {vy}"
    return True, "一致"


def adjudicate(db_path, contract, ir=None, references=None, tol=DEFAULT_TOLERANCE):
    """执行核验:列存在 → 编译 → 只读执行 → 与参照比对。返回带 evidence/status 的契约副本。

    只降不升的例外:人工授予的 certified/deprecated 不因本轮结果改变(证据照记),
    与关系的「asserted 不被数据裁决覆盖」一致。
    """
    c = dict(contract)
    ev = {"compiled_sql": "", "executed": False, "rows": 0, "reference": None, "match": None,
          "error": "", "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    human = c.get("status") in ("certified", "deprecated")
    missing = schema_missing(db_path, c)
    try:
        if missing:
            raise ValueError(";".join(missing))
        sql = compile_sql(c)
    except ValueError as exc:
        ev["error"] = str(exc)
        c["evidence"] = ev
        if not human:
            c["status"] = "candidate"
        return c
    ev["compiled_sql"] = sql
    res = execute(db_path, sql)
    if res.get("error"):
        ev["error"] = res["error"]
        c["evidence"] = ev
        if not human:
            c["status"] = "candidate"
        return c
    ev["executed"] = True
    ev["rows"] = len(res.get("rows") or [])
    if ev["rows"] == 1 and len(res["rows"][0]) == 1:
        ev["value"] = res["rows"][0][0]
    refs = [r for r in (references or []) if isinstance(r, dict)]
    best = None
    for ref in refs:
        rres = None
        if ref.get("kind") == "value":
            rres = {"columns": ["value"], "rows": [[ref.get("value")]]}
            mine = res if ev["rows"] == 1 else None
        elif ref.get("kind") == "sql" and ref.get("sql"):
            rres = execute(db_path, ref["sql"])
            if rres.get("error"):
                best = best or {"source": ref.get("source", ""), "kind": "sql",
                                "note": "参照 SQL 无法执行:" + rres["error"], "match": None}
                continue
            # 参照带分组时,按参照的形状重新编译本指标再比;分组列取参照的非数值列
            mine = res
            if len(rres.get("rows") or []) != 1 or len(rres["rows"][0]) != 1:
                dims = ref.get("dimensions") or c.get("dimensions") or []
                grain = ref.get("grain")
                try:
                    mine = execute(db_path, compile_sql(c, grain=grain, dimensions=dims))
                except ValueError as exc:
                    best = best or {"source": ref.get("source", ""), "kind": "sql",
                                    "note": f"无法按参照形状编译:{exc}", "match": None}
                    continue
                if mine.get("error"):
                    best = best or {"source": ref.get("source", ""), "kind": "sql",
                                    "note": "按参照形状执行失败:" + mine["error"], "match": None}
                    continue
        else:
            continue
        if mine is None:
            best = best or {"source": ref.get("source", ""), "kind": ref.get("kind"),
                            "note": "参照是标量而指标结果不是", "match": False}
            continue
        ok, why = compare_results(mine, rres, ref.get("tol", tol))
        rec = {"source": str(ref.get("source") or "")[:160], "kind": ref.get("kind"),
               "match": ok, "note": why}
        if ok:
            best = rec
            break
        if best is None or best.get("match") is None:
            best = rec
    ev["reference"] = best
    ev["match"] = best["match"] if best else None
    c["evidence"] = ev
    if human:
        return c
    c["status"] = "verified" if ev["match"] is True else "candidate"
    c["candidate"] = c["status"] == "candidate"
    return c


def audit(ir):
    """verified 指标的证据契约审计;certified 指标须记录授予人。返回 issues 列表。"""
    issues = []
    for layer, arr in ((ir or {}).get("metric_layers") or {}).items():
        for m in (arr or []):
            if not isinstance(m, dict):
                continue
            name = m.get("name") or m.get("id") or "?"
            st = m.get("status") or "candidate"
            ev = m.get("evidence") if isinstance(m.get("evidence"), dict) else {}
            if st == "verified":
                if not (ev.get("compiled_sql") and ev.get("executed") and ev.get("match") is True
                        and ev.get("reference")):
                    issues.append({"type": "verified_metric_missing_evidence", "metric": name, "layer": layer,
                                   "desc": f"指标「{name}」标为 verified 但缺少「编译 SQL + 执行成功 + 与参照一致」的证据",
                                   "fix": "重新执行指标核验;无参照或不一致时降为 candidate"})
            elif st == "certified" and not m.get("certified_by"):
                issues.append({"type": "certified_metric_missing_reviewer", "metric": name, "layer": layer,
                               "desc": f"指标「{name}」标为 certified 但未记录确认人",
                               "fix": "certified 只能由人授予,须记录 certified_by"})
    return issues


def counts(ir):
    out = {s: 0 for s in STATUSES}
    out["legacy"] = 0
    for arr in ((ir or {}).get("metric_layers") or {}).values():
        for m in (arr or []):
            if not isinstance(m, dict):
                continue
            if not is_contract(m):
                out["legacy"] += 1
            out[m.get("status") if m.get("status") in STATUSES else "candidate"] += 1
    return out


def upsert_layers(ir, contracts):
    """把契约并入 ir["metric_layers"]:同 id/同名者按状态取高(不降级人工结论),
    旧形状指标原样保留。返回 (metric_layers, 统计)。"""
    layers = ir.get("metric_layers") if isinstance(ir.get("metric_layers"), dict) else {}
    layers = {k: list(v) for k, v in layers.items() if isinstance(v, list)}
    for k in LAYERS:
        layers.setdefault(k, [])
    stat = {"added": 0, "updated": 0, "kept": 0}
    index = {}
    for k, arr in layers.items():
        for i, m in enumerate(arr):
            if isinstance(m, dict):
                for key in (m.get("id"), m.get("name")):
                    if key:
                        index.setdefault(str(key), (k, i))
    for c in contracts:
        hit = index.get(str(c.get("id"))) or index.get(str(c.get("name")))
        if hit is None:
            layers[c["layer"]].append(c)
            index[str(c["id"])] = (c["layer"], len(layers[c["layer"]]) - 1)
            index.setdefault(str(c["name"]), index[str(c["id"])])
            stat["added"] += 1
            continue
        k, i = hit
        old = layers[k][i]
        if status_rank(old.get("status")) > status_rank(c.get("status")):
            merged = dict(old)
            merged["evidence"] = c.get("evidence") or old.get("evidence")
            merged["provenance"] = _merge_prov(old.get("provenance"), c.get("provenance"))
            layers[k][i] = merged
            stat["kept"] += 1
        else:
            merged = dict(c)
            for key in ("certified_by", "certified_at", "review_reason", "desc", "unit", "synonyms"):
                if not merged.get(key) and old.get(key):
                    merged[key] = old[key]
            merged["provenance"] = _merge_prov(old.get("provenance"), c.get("provenance"))
            layers[k][i] = merged
            stat["updated"] += 1
    return layers, stat


def _merge_prov(a, b):
    out = []
    for p in (a or []) + (b or []):
        if isinstance(p, dict) and p not in out:
            out.append(p)
    return out[:20]
