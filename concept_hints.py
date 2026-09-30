#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""概念层线索:表与领域概念之间不是一一对应,两个方向都要在构建前识别出来(DR-064)。

- **合并**(表族):多厂、多层、多站点的数据湖仓里,同一实体按「层级_厂码_来源_业务词根_变体」
  复制成几十张表。同一业务词根的表是同一个概念的成员。
- **拆分**(类别子集):元数据驱动的业务库里,一张实例表按类别编码装着多种概念
  (设备表里的电能表、流量计、逆变器),类别目录表给出每一类的名称。

另有两类「有效行」规律,两种场景各占一种,处理方式相同——先定哪些行算数,再判唯一与重叠:
软删除(已删除行仍在表里)与版本重复(同一业务键多次入仓,保留最新一条)。

所有识别都是确定性的,只读,不调模型。厂码、站点、来源这类前缀不写死,
从表名里学:一个词后面接的剩余部分若也常出现在别的词后面,并且它后面接的表足够多,
它就是可替换的前缀。数仓层级(ods/dwd/dim…)是通用约定,直接内置。
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

LAYER = re.compile(r"^(ods|stg|dwd|dws|dwm|dim|ads|dm|app|mid)_", re.I)
LAYER_RANK = {"dwd": 0, "dim": 1, "dws": 2, "dwm": 2, "ads": 3, "ods": 4, "stg": 5}
NOISE = re.compile(r"_(offline|bak\d*|backup|copy\d*|old|new|tmp|temp\d*|test\d*|his|v\d+|rt|ts|"
                   r"(19|20)\d{2}(\d{2}){0,2}(bak)?|\d{3,8}|\d+d\d+)$", re.I)
GENERIC = {"detail", "details", "info", "log", "logs", "data", "list", "record", "records", "base", "item",
           "items", "config", "result", "main", "day", "month", "table", "view"}
# 短而常见的业务尾词:它们区分实体(标签 vs 标签对象、设备 vs 设备关系),不是副本标记
TAIL_WORDS = {"obj", "rel", "log", "map", "cfg", "set", "sub", "top", "sum", "avg", "day", "hot", "sn", "no", "id"}
TIME_COL = re.compile(r"^(insert_time|update_time|updated_at|update_at|edit_time|modify_time|gmt_modified|"
                      r"create_time|create_at|created_at|gmt_create|dt)$", re.I)
BUSINESS_KEY = re.compile(r"(_no|_number|_code|_sn|_id)$", re.I)
CATEGORY_COL = re.compile(r"(type_code|category_code|class_code|kind_code|_type)$", re.I)
NAME_COL = re.compile(r"(_name|^name|_label|^label|_desc)$", re.I)


def _bare(table):
    return table.split("__", 1)[-1]


def clean_name(table):
    """去库名前缀、数仓层级与噪声后缀(备份、日期、离线副本、数字变体)。"""
    t = LAYER.sub("", _bare(table).lower())
    prev = None
    while prev != t:
        prev = t
        t = NOISE.sub("", t)
        m = re.match(r"^(.*_)?([a-z]{3,})(\d{1,4})$", t)
        if m:
            t = (m.group(1) or "") + m.group(2)
    return t or _bare(table).lower()


def _ok_rest(rest):
    r = "_".join(rest)
    return not (len(rest) == 1 and (len(r) < 4 or r in GENERIC))


def _substitutable(cur, side, min_rem, ratio=0.5):
    after, owners = defaultdict(set), defaultdict(set)
    for tk in cur.values():
        if len(tk) < 2:
            continue
        x, r = (tk[0], "_".join(tk[1:])) if side == 0 else (tk[-1], "_".join(tk[:-1]))
        after[x].add(r)
        owners[r].add(x)
    out = set()
    for x, rs in after.items():
        if x.isdigit():
            out.add(x)
        elif len(rs) >= min_rem and sum(1 for r in rs if len(owners[r]) >= 2) / len(rs) >= ratio:
            out.add(x)
    return out


def name_cores(tables, rounds=5):
    """表名 → 业务词根。返回 (词根映射, 学到的前缀, 学到的后缀)。

    前缀要后面接足够多种表(≥10 且 ≥1% 表数):厂码、来源后面接上百种表,
    「fct_/aoi_」这类子类型只接几种,不能剥,否则不同的测试会并成一族。
    后缀只学短的变体标记(数字或 ≤3 个字母),业务词(area、type)不剥。
    """
    cur = {t: clean_name(t).split("_") for t in tables}
    min_rem = max(10, len(tables) // 100)
    pre, suf = set(), set()
    for _ in range(rounds):
        pre |= _substitutable(cur, 0, min_rem)
        suf |= {x for x in _substitutable(cur, 1, 3, ratio=0.8)
                if x.isdigit() or (len(x) <= 3 and x not in TAIL_WORDS)}
        changed = False
        for t, tk in cur.items():
            while len(tk) > 1 and tk[0] in pre and _ok_rest(tk[1:]):
                tk, changed = tk[1:], True
            while len(tk) > 1 and tk[-1] in suf and tk[-1] not in GENERIC and _ok_rest(tk[:-1]):
                tk, changed = tk[:-1], True
            for i, x in enumerate(tk[:5]):          # 站点段:「来源_站点_编号_」整段剥到编号为止
                if x.isdigit() and i + 1 < len(tk) and _ok_rest(tk[i + 1:]):
                    tk, changed = tk[i + 1:], True
                    break
            cur[t] = tk
        if not changed:
            break
    return {t: "_".join(tk) for t, tk in cur.items()}, sorted(pre), sorted(suf)


def envelope(cols, share=0.2):
    """被 ≥20% 的表共用的列:时间、审计、租户、分区等信封列,不具备实体区分度。"""
    n = max(1, len(cols))
    cnt = Counter(c.lower() for cs in cols.values() for c in set(cs))
    return {c for c, k in cnt.items() if k >= max(3, share * n)}


def _layer(table):
    m = LAYER.match(_bare(table).lower())
    return m.group(1).lower() if m else ""


def table_families(cols, rows=None):
    """同一业务词根的表归为一族(一个概念);单个短词根还要求业务列相似,防止误并。

    返回按成员数降序的族列表;只含 ≥2 张表的族。代表表按层级(dwd > dim > dws > ods > stg)、
    行数、列数挑选,分轮构建时只让代表表进入计划,其余成员挂到同一个概念上。
    """
    rows = rows or {}
    cores, pre, suf = name_cores(list(cols))
    env = envelope(cols)
    biz = {t: {c.lower() for c in cs} - env for t, cs in cols.items()}
    groups = defaultdict(list)
    for t, core in cores.items():
        groups[core].append(t)
    fams = []
    for core, members in groups.items():
        if len(members) < 2:
            continue
        # 代表表:正本优先(不带备份/离线/日期后缀),再按层级、行数、列数
        rank = lambda t: (bool(NOISE.search(_bare(t))), LAYER_RANK.get(_layer(t), 6),
                          -(rows.get(t) or 0), -len(cols[t]), t)
        members = sorted(members, key=rank)
        if len(core.split("_")) < 2:
            rep = members[0]
            keep = [rep] + [m for m in members[1:]
                            if len(biz[rep] & biz[m]) / max(1, len(biz[rep] | biz[m])) >= 0.2]
            if len(keep) < 2:
                continue
            members = keep
        fams.append({"core": core, "representative": members[0], "members": members,
                     "layers": sorted({_layer(t) or "-" for t in members})})
    fams.sort(key=lambda f: (-len(f["members"]), f["core"]))
    return {"families": fams, "prefixes": pre, "suffixes": suf,
            "tables_in_families": sum(len(f["members"]) for f in fams)}


def _q(name):
    return '"' + str(name).replace('"', '""') + '"'


def _names_match(stem, table):
    """类别列的词干要能在目录表名里找到(dev ↔ device、sys ↔ system:按前缀匹配)。"""
    toks = [x for x in _bare(table).lower().split("_") if len(x) >= 3]
    return any(x.startswith(w) or w.startswith(x) for w in stem for x in toks)


def category_splits(con, cols, rows=None, max_categories=60, max_catalog_rows=5000):
    """实例表按类别编码装着多种概念:找「类别列 → 类别目录表」,给出每类的名称与行数。

    类别目录 = 另一张不大的表(≤5000 行),有与类别列同尾缀的编码列(取值基本唯一)和名称列,
    其编码覆盖实例表 ≥90% 的类别值;不能是实例表自己的同族副本(列几乎相同)。
    """
    rows = rows or {}
    catalogs = []
    for t, cs in cols.items():
        n = rows.get(t) or 0
        if not n or n > max_catalog_rows:
            continue
        for c in cs:
            if not (CATEGORY_COL.search(c) or c.lower().endswith("_code")):
                continue
            stem = re.sub(r"_code$", "", c.lower())
            name = next((x for x in cs if x.lower() in (stem + "_name", stem.rsplit("_", 1)[0] + "_name")), None) \
                or next((x for x in cs if NAME_COL.search(x)), None)
            if name:
                catalogs.append((t, c, name))
    cache = {}

    def catalog_values(ct, cc, cn):
        if (ct, cc) not in cache:
            try:
                got = con.execute(f"SELECT {_q(cc)}, MAX({_q(cn)}), COUNT(*) FROM {_q(ct)} "
                                  f"WHERE {_q(cc)} IS NOT NULL GROUP BY 1").fetchall()
            except Exception:
                got = []
            total = sum(r[2] for r in got)
            # 编码基本唯一:软删除会让同一编码出现多行,放宽到每个编码平均不超过 3 行
            cache[(ct, cc)] = {str(k): v for k, v, _ in got} if got and total <= 3 * len(got) else {}
        return cache[(ct, cc)]

    out = []
    for t, cs in cols.items():
        if not rows.get(t):
            continue
        lower = {c.lower() for c in cs}
        for c in cs:
            if not CATEGORY_COL.search(c):
                continue
            tail = c.lower().split("_", 1)[-1] if c.lower().count("_") >= 2 else c.lower()
            stem = [x for x in c.lower().split("_") if x not in ("type", "code", "category", "class", "kind")
                    and len(x) >= 3]
            if not stem:
                continue
            cands = [(ct, cc, cn) for ct, cc, cn in catalogs
                     if ct != t and cc.lower().endswith(tail) and _names_match(stem, ct)
                     and len(lower & {x.lower() for x in cols[ct]}) / max(1, len(lower | {x.lower() for x in cols[ct]})) < 0.5]
            if not cands:
                continue
            try:
                dist = con.execute(f"SELECT {_q(c)}, COUNT(*) FROM {_q(t)} WHERE {_q(c)} IS NOT NULL "
                                   f"AND TRIM({_q(c)})<>'' GROUP BY 1 ORDER BY 2 DESC").fetchall()
            except Exception:
                continue
            if not (2 <= len(dist) <= max_categories):
                continue
            total = sum(n for _, n in dist)
            best = None
            for ct, cc, cn in cands:
                cat = catalog_values(ct, cc, cn)
                if not cat:
                    continue
                cover = sum(n for v, n in dist if str(v) in cat) / total
                named = {str(cat.get(str(v)) or "").strip() for v, _ in dist[:12]} - {""}
                if len(named) < 2:          # 各类名称都一样(或为空)的不是类别目录
                    continue
                if cover >= 0.9 and (best is None or cover > best[0]):
                    best = (cover, ct, cc, cn, cat)
            if best:
                cover, ct, cc, cn, cat = best
                out.append({"table": t, "column": c, "catalog": ct, "catalog_key": cc, "catalog_name": cn,
                            "coverage": round(cover, 3),
                            "categories": [{"code": str(v), "name": cat.get(str(v)) or "", "rows": n}
                                           for v, n in dist[:12]]})
    out.sort(key=lambda s: -sum(x["rows"] for x in s["categories"]))
    return out


def version_duplicates(con, cols, rows=None, max_tables=400):
    """业务键几乎唯一但有少量重复、且表里有时间列:多为同一实体多次入仓(版本重复)。

    「父键不唯一」会让数据裁决拒绝关系;这类键按「键内保留最新一条」后是唯一的,
    与软删除一样,属于先定有效行的问题。
    """
    rows = rows or {}
    out = []
    for t, cs in list(cols.items())[:max_tables]:
        n = rows.get(t) or 0
        tcol = next((c for c in cs if TIME_COL.match(c)), None)
        if n < 20 or not tcol:
            continue
        ttoks = set(_bare(t).lower().split("_"))
        for c in cs:
            if c.lower() == "id" or not BUSINESS_KEY.search(c) or TIME_COL.match(c):
                continue
            # 业务键要与表名对得上(工单号之于工单表);关联表里的外键本来就可以重复
            stem = [x for x in re.sub(r"_(no|number|code|sn|id)$", "", c.lower()).split("_") if x]
            if not stem or not all(x in ttoks for x in stem):
                continue
            try:
                d = con.execute(f"SELECT COUNT(DISTINCT {_q(c)}) FROM {_q(t)} WHERE {_q(c)} IS NOT NULL").fetchone()[0]
            except Exception:
                continue
            if d and 0.95 * n <= d < n:
                dup = n - d
                out.append({"table": t, "key": c, "rows": n, "distinct": d, "duplicate_rows": dup,
                            "time_column": tcol,
                            "action": f"按 {c} 分组、{tcol} 取最新一条后再判唯一与重叠"})
                break
    out.sort(key=lambda v: -v["duplicate_rows"])
    return out


def text_for_prompt(hints, scope=None, cap=2500):
    """给提议模型的概念层线索(只含本轮范围内的表)。"""
    scope = set(scope or [])
    lines = []
    for f in hints.get("families", []):
        ms = f["members"]
        if scope and not scope & set(ms):
            continue
        lines.append(f"- 表族「{f['core']}」{len(ms)} 张表是同一概念的多份(多厂/多层/多站点/备份):"
                     f"代表 {f['representative']};建一个对象,不要逐表建")
    for s in hints.get("category_splits", []):
        if scope and s["table"] not in scope:
            continue
        cats = "、".join(f"{c['code']}={c['name'] or '?'}({c['rows']})" for c in s["categories"][:8])
        lines.append(f"- {s['table']}.{s['column']} 按类别装着多种概念(目录 {s['catalog']}):{cats};"
                     "按类别建子概念或在对象说明中写明类别,不要只建一个笼统对象")
    for v in hints.get("version_duplicates", []):
        if scope and v["table"] not in scope:
            continue
        lines.append(f"- {v['table']}.{v['key']} 有 {v['duplicate_rows']} 行版本重复:{v['action']}")
    if not lines:
        return ""
    return ("[概念层线索(构建体检自动识别;表与概念不是一一对应)]\n" + "\n".join(lines))[:cap]


def collect(db, max_tables=5000):
    """只读打开一个 SQLite 数据源,一次取齐三类线索(表族、类别拆分、版本重复)。"""
    import sqlite3
    from pathlib import Path
    con = sqlite3.connect(Path(db).absolute().as_uri() + "?mode=ro", uri=True)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "AND name NOT IN ('_dm_tables','_dm_columns') ORDER BY name")][:max_tables]
        cols = {t: [r[1] for r in con.execute(f"PRAGMA table_info({_q(t)})")] for t in tables}
        rows = {t: con.execute(f"SELECT COUNT(*) FROM {_q(t)}").fetchone()[0] for t in tables}
        fam = table_families(cols, rows)
        return {**fam, "category_splits": category_splits(con, cols, rows),
                "version_duplicates": version_duplicates(con, cols, rows), "tables": len(tables)}
    finally:
        con.close()


def summary_text(hints):
    fams = hints.get("families") or []
    parts = []
    if fams:
        parts.append(f"表族 {len(fams)} 个(收编 {hints.get('tables_in_families', 0)} 张表)")
    if hints.get("category_splits"):
        parts.append(f"按类别拆分 {len(hints['category_splits'])} 处")
    if hints.get("version_duplicates"):
        parts.append(f"版本重复 {len(hints['version_duplicates'])} 张表")
    return "概念层线索 · " + (" · ".join(parts) if parts else "表与概念基本一一对应")


def attach_families(ir, hints):
    """构建产物里,绑定到某个表族成员的对象挂上全族成员表:一个概念,多张表。

    同一族被建成多个对象时不删除,只在后来者上标出与谁同族,留给复核合并。返回统计。
    """
    by_table = {t: f for f in hints.get("families") or [] for t in f["members"]}
    owner, attached, dup = {}, 0, 0
    for o in ir.get("objects") or []:
        t = o.get("table") or ((o.get("tables") or [None])[0])
        f = by_table.get(t)
        if not f:
            continue
        ev = o.setdefault("evidence", {})
        if f["core"] in owner:
            ev["family_duplicate_of"] = owner[f["core"]]
            dup += 1
            continue
        owner[f["core"]] = o.get("name")
        o["tables"] = [t] + [m for m in f["members"] if m != t]
        ev["family"] = {"core": f["core"], "representative": f["representative"], "members": len(f["members"])}
        attached += 1
    return {"objects": attached, "duplicates": dup}
