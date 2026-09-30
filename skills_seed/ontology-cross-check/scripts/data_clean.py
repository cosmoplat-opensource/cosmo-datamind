# -*- coding: utf-8 -*-
"""数据清洗:把编码冲突、悬空引用、迁移空壳、注释与取值不符变成带证据的清洗规则。

这些问题在业务库里反复出现,不能每次都靠人去反馈。本模块只读源库,产出声明式规则:
每条规则写明问题、证据、建议动作和一段可直接套用的 SQL 片段(清洗视图用),
由构建、问数和人工复核共同消费;源库一律不改。

- 码表:从字典类表与字段注释中收集码表,按取值覆盖率与字段名相似度判断每个编码字段用的是哪套;
  同名字段用了不同码表时,按标签给出码表之间的对照。
- 离群编码:同一类别下编码应当一致(函数依赖)。类别可来自本表,也可沿已成立的关系取父表类别,
  例如「计量器具类别 → 能源类型」;少数不一致的行给出按多数值更正的建议。
- 悬空引用:关系成立,但部分子记录找不到父记录;给出比例与排除条件。
- 迁移空壳:同名表在多个库中,一份为空、一份有数据;建议绑定有数据的那份。
- 注释编码:字段注释列出了编码,但数据里出现了注释没写的值。
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

_ENUM = re.compile(r"(?<![\w.])(-?\d{1,3})\s*[-:：=、.]?\s*([A-Za-z一-龥][\w一-龥]{0,11})")
_GROUP = ("dict_code", "dict_type_code", "dict_type", "dict_id", "type_code")
_VALUE = ("value", "item_value", "dict_value", "dict_entry_code", "dict_key", "code")
_LABEL = ("label", "item_text", "dict_label", "dict_entry_name", "name")
# 决定方:实体类别编码(设备类别、系统类别、表计类型……);被决定方:业务编码(能源类型、介质……)。
# 状态、开关、删除标记、等级这类列在同一类别下本来就会变化,不参与离群判断。
_CATEGORY = re.compile(r"(type_code|category|class_code|kind)$", re.I)
_VOLATILE = re.compile(r"(status|state|flag|^is_|del|show|enable|level|mode|required|reset|recovery|sort|input)", re.I)
MIN_GROUP, MODE_SHARE, FD_STRENGTH = 10, 0.8, 0.9
MIN_DEP_VALUES, MIN_PROBE_MATCH = 3, 30
BOOK_COVER = 0.9


def _q(name):
    return '"' + str(name).replace('"', '""') + '"'


def _norm(s):
    return re.sub(r"[^0-9a-z一-龥]", "", str(s).lower())


def _bare(table):
    return table.split("__", 1)[-1]


def _rows(db, sql, args=()):
    try:
        return db.con.execute(sql, args).fetchall()
    except Exception:
        return []


def comment_books(con):
    """_dm_columns 里的字段注释若列出了编码(如「1 电、2 水」),就是这个字段自己的码表。"""
    try:
        rows = con.execute("SELECT table_name, column_name, comment FROM _dm_columns").fetchall()
    except Exception:
        return {}
    out = {}
    for t, c, cm in rows:
        pairs = _ENUM.findall(cm or "")
        book = {}
        for code, label in pairs:
            book.setdefault(code, label)
        if len(book) >= 2:
            out[(t, c)] = book
    return out


def dict_books(db):
    """字典类表:有分组列、取值列、标签列三件套的表,每个分组是一套码表。"""
    books = {}
    for t in db.tables:
        if "dict" not in t.lower():
            continue
        g = next((db.col(t, c) for c in _GROUP if db.col(t, c)), None)
        v = next((db.col(t, c) for c in _VALUE if db.col(t, c) and db.col(t, c) != g), None)
        lab = next((db.col(t, c) for c in _LABEL if db.col(t, c) and db.col(t, c) not in (g, v)), None)
        if not (g and v and lab):
            continue
        for grp, val, label in _rows(db, f"SELECT {_q(g)}, {_q(v)}, {_q(lab)} FROM {_q(t)}"):
            if grp is None or val is None:
                continue
            books.setdefault(f"{_bare(t)}:{grp}", {}).setdefault(str(val), str(label))
    return {k: b for k, b in books.items() if len(b) >= 2}


def _values(db, t, c):
    """编码列的取值分布;逗号分隔的多选值拆开计数。"""
    cnt = Counter()
    for v, n in _rows(db, f"SELECT {_q(c)}, COUNT(*) FROM {_q(t)} WHERE {_q(c)} IS NOT NULL AND TRIM({_q(c)})<>'' GROUP BY 1"):
        for part in str(v).split(","):
            if part.strip():
                cnt[part.strip()] += n
    return cnt


def code_columns(db, comments):
    cols = []
    for t in db.tables:
        for c in db.cols(t):
            if (t, c) in comments or re.search(r"(_type|type_value|_level|_status)$", c, re.I):
                cols.append((t, c))
    return cols


def assign_books(db, comments, books):
    """每个编码字段用哪套码表:取值覆盖率 ≥ 90%,且码表名与字段名相符或码表就是字段自己的注释。
    0/1 这类取值任何码表都能覆盖,只凭覆盖率会误配;注释可能写错,覆盖率不够就不采信。"""
    out = {}
    for t, c in code_columns(db, comments):
        vals = _values(db, t, c)
        total = sum(vals.values())
        if not total or len(vals) > 40:
            continue
        cands = dict(books)
        if (t, c) in comments:
            cands[f"注释:{_bare(t)}.{c}"] = comments[(t, c)]
        best, fits = None, []
        for name, book in cands.items():
            cover = sum(n for v, n in vals.items() if v in book) / total
            if cover < BOOK_COVER:
                continue
            sim = 1.0 if _norm(c) in _norm(name.split(":")[-1]) or _norm(name.split(":")[-1]) in _norm(c) else 0.0
            own = 0.5 if name == f"注释:{_bare(t)}.{c}" else 0.0
            # 只凭覆盖率不够:0/1 这类取值任何码表都能覆盖。必须有字段名或字段自己的注释支持
            if not (sim or own):
                continue
            score = (sim + own + cover, -len(book))
            fits.append(name)
            if best is None or score > best[0]:
                best = (score, name, cover)
        if best:
            # 另一套也能覆盖取值、且同一编码含义不同(常见于字段注释与字典不一致):不替它选,标为待定
            rivals = [n for n in fits if n != best[1] and any(_norm(cands[n].get(k, "")) != _norm(v)
                      for k, v in cands[best[1]].items() if k in cands[n] and k in vals)]
            own_rival = [n for n in rivals if n.startswith("注释:")]
            out[(t, c)] = {"book": best[1], "coverage": round(best[2], 3), "values": dict(vals.most_common(12)),
                           "ambiguous_with": own_rival[:3]}
    return out


def crosswalk(a, b):
    """两套码表按标签对照:标签相同或一方包含另一方(水 / 自来水)即视为同一介质。"""
    pairs = {}
    for ca, la in a.items():
        for cb, lb in b.items():
            na, nb = _norm(la), _norm(lb)
            if na and nb and (na == nb or (min(len(na), len(nb)) >= 1 and (na in nb or nb in na))):
                pairs.setdefault(ca, cb)
    return pairs


def encoding_conflicts(assigned, books, comments):
    """同名字段在不同表里用了不同的码表,且同一个编码指的是不同的东西。"""
    allb = dict(books)
    for (t, c), bk in comments.items():
        allb[f"注释:{_bare(t)}.{c}"] = bk
    by_col = defaultdict(list)
    for (t, c), a in assigned.items():
        by_col[c.lower()].append((t, c, a["book"]))
    rules = []
    for col, items in by_col.items():
        if _VOLATILE.search(col):          # 状态、删除标记这类列各表自定含义,同名不同义属正常
            continue
        names = sorted({b for _, _, b in items})
        if len(names) < 2:
            continue
        clashes = []
        for i, x in enumerate(names):
            for y in names[i + 1:]:
                bx, by = allb.get(x, {}), allb.get(y, {})
                diff = [k for k in set(bx) & set(by) if _norm(bx[k]) != _norm(by[k])]
                if diff:
                    clashes.append({"books": [x, y], "same_code_different_meaning": sorted(diff)[:8],
                                    "crosswalk": crosswalk(bx, by)})
        if clashes:
            rules.append({"kind": "encoding_conflict", "column": col,
                          "usage": [{"table": t, "column": c, "book": b} for t, c, b in items],
                          "clashes": clashes,
                          "action": "按字段实际使用的码表解释取值;跨表比较或汇总前先按对照表换算,不合并码表"})
    return rules


def _fd(pairs):
    """pairs: [(类别值, 编码值, 行数)] → 各类别的多数编码与离群行。"""
    groups = defaultdict(Counter)
    for d, v, n in pairs:
        if d is not None and v is not None:
            groups[d][str(v)] += n
    total = kept = 0
    outliers = []
    for d, cnt in groups.items():
        n = sum(cnt.values())
        mode, m = cnt.most_common(1)[0]
        total += n
        kept += m
        if n >= MIN_GROUP and m / n >= MODE_SHARE and m < n:
            outliers.append({"category": d, "expected": mode, "support": round(m / n, 3), "rows": n,
                             "outliers": {k: v for k, v in cnt.items() if k != mode}})
    return (kept / total if total else 0.0), outliers


def _categories(db, t, exclude):
    out = []
    for c in db.cols(t):
        if c in exclude or c.lower() == "id" or not _CATEGORY.search(c):
            continue
        r = db.one(f"SELECT COUNT(DISTINCT {_q(c)}) FROM {_q(t)}")
        if r and 2 <= r[0] <= 200:
            out.append(c)
    return out


def outlier_codes(db, assigned, relations, coherent):
    """类别决定编码:同一类别下编码应当一致,少数不一致的行是标错。
    类别先在本表找,再沿已成立的关系到父表找(子表常常只存了父记录编号)。"""
    rel_by_via = {(r["child"], r["parent"], r["child_key"], r["parent_key"]): r for r in relations}
    rules = []
    for (t, c), a in assigned.items():
        if _VOLATILE.search(c) or a["book"].startswith("注释:") and not re.search(r"_type$", c) \
                or len(a["values"]) < MIN_DEP_VALUES:
            continue
        tries = [(f"{t}.{d}", f"SELECT {_q(d)}, {_q(c)}, COUNT(*) FROM {_q(t)} GROUP BY 1, 2", (None, None, None, d, None))
                 for d in _categories(db, t, {c})]
        for rel in relations:
            if rel["child"] != t:
                continue
            pt, ck, pk, cond = rel["parent"], rel["child_key"], rel["parent_key"], rel.get("where", "")
            for d in _categories(db, pt, {pk}):
                tries.append((f"{pt}.{d}(经 {ck}→{pk})",
                              f"SELECT p.{_q(d)}, c.{_q(c)}, COUNT(*) FROM {_q(t)} c JOIN {_q(pt)} p "
                              f"ON p.{_q(pk)}=c.{_q(ck)} {('WHERE c.' + cond) if cond else ''} GROUP BY 1, 2",
                              (pt, ck, pk, d, rel.get("cond"))))
        for label, sql, via in tries:
            strength, outs = _fd(_rows(db, sql))
            if strength < FD_STRENGTH:
                continue
            if not outs:
                if via[0] and rel_by_via.get((t,) + via[:3], {}).get("probe"):
                    coherent.append({"kind": "coherent_dangling_relation", "table": t, "column": via[1],
                                     "parent": f"{via[0]}.{via[2]}", "condition": rel_by_via[(t,) + via[:3]].get("where", ""),
                                     "evidence": f"{label} 决定 {c},一致率 {round(strength, 3)}",
                                     "action": "关系按条件成立,悬空部分单独报告;建议升为待复核而非丢弃"})
                continue
            n_out = sum(sum(o["outliers"].values()) for o in outs)
            if via[0] and rel_by_via.get((t,) + via[:3], {}).get("probe"):
                # 重叠率不达标的关系,若父表类别仍强烈决定子表编码,说明关联在语义上成立,
                # 低重叠来自悬空引用而不是连错了表
                coherent.append({"kind": "coherent_dangling_relation", "table": t, "column": via[1],
                                 "parent": f"{via[0]}.{via[2]}", "condition": rel_by_via[(t,) + via[:3]].get("where", ""),
                                 "evidence": f"{label} 决定 {c},一致率 {round(strength, 3)}",
                                 "action": "关系按条件成立,悬空部分单独报告;建议升为待复核而非丢弃"})
            rule = {"kind": "outlier_code", "table": t, "column": c, "determined_by": label,
                    "fd_strength": round(strength, 3), "outlier_rows": n_out, "groups": outs[:10],
                    "action": "按类别的多数编码更正(清洗视图中生效,源库不改);更正前先看离群行是否另有业务原因"}
            pt, ck, pk, d, cond = via
            # 结构化字段供取数侧(清洗视图)重建 SQL 并逐一校验标识符;sql 只供人读
            rule["category"] = {"table": pt or t, "column": d,
                                "via": {"child_key": ck, "parent_key": pk} if pt else None,
                                "condition": {"column": cond[0], "value": cond[1]} if cond else None}
            rule["sql"] = recode_expression(rule)
            rules.append(rule)
            break
    return rules


def _lit(v):
    return "NULL" if v is None else "'" + str(v).replace("'", "''") + "'"


def recode_expression(rule, quote=_q):
    """离群编码规则 → 清洗视图中的列表达式。只用结构化字段,取值一律按字面量转义。"""
    t, c, cat = rule["table"], rule["column"], rule["category"]
    if cat.get("via"):
        probe = (f"(SELECT p.{quote(cat['column'])} FROM {quote(cat['table'])} p "
                 f"WHERE p.{quote(cat['via']['parent_key'])}={quote(t)}.{quote(cat['via']['child_key'])} LIMIT 1)")
    else:
        probe = f"{quote(t)}.{quote(cat['column'])}"
    guard = ""
    if cat.get("condition"):
        guard = f"{quote(t)}.{quote(cat['condition']['column'])}={_lit(cat['condition']['value'])} AND "
    cases = " ".join(f"WHEN {guard}{probe}={_lit(g['category'])} THEN {_lit(g['expected'])}" for g in rule["groups"][:20])
    return f"CASE {cases} ELSE {quote(t)}.{quote(c)} END"


def codebooks(assigned, books, comments, bound_tables):
    """本体绑定表上的编码字段各用哪套码表:问数时把码表给模型,避免把「2」当成水。"""
    allb = dict(books)
    for (t, c), bk in comments.items():
        allb[f"注释:{_bare(t)}.{c}"] = bk
    out = []
    for (t, c), a in assigned.items():
        book = allb.get(a["book"]) or {}
        if t in bound_tables and 2 <= len(book) <= 40:
            used = {k: book[k] for k in a["values"] if k in book}
            out.append({"kind": "codebook", "table": t, "column": c, "book": a["book"], "coverage": a["coverage"],
                        "labels": used or dict(list(book.items())[:20]), "ambiguous_with": a.get("ambiguous_with", [])})
    return out


def orphans(db, relations):
    rules = []
    for rel in relations:
        ct, pt, ck, pk, cond = rel["child"], rel["parent"], rel["child_key"], rel["parent_key"], rel.get("where", "")
        where = f" AND {cond}" if cond else ""
        r = db.one(f"""SELECT COUNT(*), SUM(CASE WHEN EXISTS(SELECT 1 FROM {_q(pt)} p WHERE p.{_q(pk)}=c.{_q(ck)}) THEN 0 ELSE 1 END)
                       FROM {_q(ct)} c WHERE c.{_q(ck)} IS NOT NULL AND TRIM(c.{_q(ck)})<>''{where}""")
        if not r or not r[0] or not r[1]:
            continue
        rate = round(r[1] / r[0], 3)
        rules.append({"kind": "orphan_reference", "table": ct, "column": ck, "parent": f"{pt}.{pk}",
                      "condition": cond, "rows": r[0], "orphan_rows": r[1], "orphan_rate": rate,
                      "severity": "high" if rate > 0.3 else "low",
                      "action": "沿此关系取数时排除悬空行并报告比例;比例高时关系仍按条件成立,但不能用于完整性汇总",
                      "sql": f"EXISTS (SELECT 1 FROM {_q(pt)} p WHERE p.{_q(pk)}={_q(ct)}.{_q(ck)})"})
    return rules


def empty_shells(db, bound_tables):
    groups = defaultdict(list)
    for t in db.tables:
        groups[_bare(t)].append(t)
    rules = []
    for bare, ts in groups.items():
        if len(ts) < 2:
            continue
        counts = {t: (db.one(f"SELECT COUNT(*) FROM {_q(t)}") or [0])[0] for t in ts}
        full = [t for t, n in counts.items() if n]
        empty = [t for t, n in counts.items() if not n]
        if full and empty:
            rules.append({"kind": "empty_shell", "table": bare, "populated": full, "empty": empty,
                          "bound_to_empty": sorted(set(empty) & bound_tables),
                          "action": "对象绑定有数据的那一份;空表不参与裁决与取数"})
    return rules


def unknown_codes(db, comments, assigned):
    rules = []
    for (t, c), book in comments.items():
        if t not in db.tables or not db.col(t, c):
            continue
        vals = _values(db, t, c)
        extra = {v: n for v, n in vals.items() if v not in book}
        if extra and sum(extra.values()) > 0:
            used = assigned.get((t, c), {}).get("book", "")
            rules.append({"kind": "comment_code_mismatch", "table": t, "column": c, "comment_book": book,
                          "unlisted_values": dict(Counter(extra).most_common(8)),
                          "explained_by": used if used and not used.startswith("注释:") else "",
                          "action": ("取值由另一套码表解释,以实际码表为准,注释视为有误" if used and not used.startswith("注释:")
                                     else "注释未列出的编码送人工确认含义,确认前按「未知」处理")})
    return rules


def clean(db, comments_con, relations, bound_tables):
    """relations: [{child, parent, child_key, parent_key, where, probe}] —— 已验证或按条件成立的关系;
    probe=True 的是重叠率不达标、但匹配量足够的候选,只有语义一致(类别决定编码)时才沿用。"""
    comments = comment_books(comments_con)
    books = dict_books(db)
    assigned = assign_books(db, comments, books)
    coherent = []
    outs = outlier_codes(db, assigned, relations, coherent)
    seen, dedup = set(), []
    for r in coherent:
        key = (r["table"], r["column"], r["parent"])
        if key not in seen:
            seen.add(key)
            dedup.append(r)
    ok = {(r["table"], r["column"], r["parent"]) for r in dedup}
    trusted = [r for r in relations if not r.get("probe") or (r["child"], r["child_key"], f"{r['parent']}.{r['parent_key']}") in ok]
    rules = (codebooks(assigned, books, comments, bound_tables) + encoding_conflicts(assigned, books, comments) + outs + dedup
             + orphans(db, trusted) + empty_shells(db, bound_tables) + unknown_codes(db, comments, assigned))
    return {"books": len(books), "comment_books": len(comments),
            "assigned": {f"{t}.{c}": a for (t, c), a in assigned.items()}, "rules": rules}
