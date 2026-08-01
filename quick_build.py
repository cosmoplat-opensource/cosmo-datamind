#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""quick_build.py <sqlite.db> <out_ir.json> <图谱名>
数据驱动快速建本体(沿用平台反造假规则):表→对象;关系=声明FK ∪ (命名配对+取值重叠≥60%→verified,否则candidate)。
产物为 DataMind 图谱 IR,可直接在 UI 可视化;深加工可再走 ontology-build / gov-app-ontology-build 技能。"""
import json, re, sqlite3, sys

db, out, name = sys.argv[1], sys.argv[2], sys.argv[3]
# 只读打开(mode=ro):建本体只取数、绝不改源库;缺库时响亮失败而非静默新建空库
con = sqlite3.connect(f"file:{db}?mode=ro", uri=True); con.row_factory = sqlite3.Row
qi = lambda s: '"' + str(s).replace('"', '""') + '"'   # 安全转义 SQL 标识符(列名/表名含引号也不破格)
tabs = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
print(f"[quick_build] {len(tabs)} 张表", flush=True)

_KIND_BFO = {"object": "MaterialEntity", "event": "Process"}   # kind→BFO 上层范畴(IOF 借鉴)
objects, links = [], []
cols_of, pk_of = {}, {}
for t in tabs:
    try:
        info = con.execute(f'PRAGMA table_info({qi(t)})').fetchall()
    except Exception:
        continue                                        # 表名异常不再让整轮构建崩溃
    cols_of[t] = [(r[1], r[2] or "TEXT") for r in info]
    pks = [r[1] for r in info if r[5]]
    pk_of[t] = pks[0] if pks else None
    kind = "event" if re.match(r"(dws_|fact_.*(log|record|output|iot))", t, re.I) else "object"
    objects.append({"name": t, "kind": kind, "tables": [t], "field_count": len(info),
                    "indicators": [], "remark": f"quick_build 自 {t}",
                    # ── IOF-AV 机读注释(数据驱动路径无 LLM 定义,标为原始概念,保持与多模态路径同构)──
                    "bfo": _KIND_BFO.get(kind, "MaterialEntity"), "definition": "", "isPrimitive": True,
                    "example": "", "counterExample": "", "maturity": "Provisional",
                    "provenance": {"directSource": t, "adaptedFrom": [], "excerptedFrom": None}})
    # 声明 FK(部分-引用关系,接地到 relatedToAtSomeTime)
    for fk in con.execute(f'PRAGMA foreign_key_list({qi(t)})'):
        links.append({"source_concept": t, "target_concept": fk[2], "verb": "关联",
                      "status": "verified", "overlap": None, "note": f"声明FK {fk[3]}→{fk[4]}",
                      "founded_relation": "relatedToAtSomeTime", "temporal": "atSomeTime"})

def distinct(t, c, cap=20000):
    try:
        return set(r[0] for r in con.execute(f'SELECT DISTINCT {qi(c)} FROM {qi(t)} LIMIT {cap}') if r[0] not in (None, ""))
    except Exception: return set()

_UNIQ_CACHE_MAX = 4096          # 容量上限:超出即整体清空(退化为不缓存,只损性能不损正确性)
_uniq_cache = {}
def is_key_unique(t, c):
    """父连接键须为候选键(值唯一)才构成真 FK。

    声明 PK 短路不查库;COUNT 结果按 (表,列) 缓存——同一父键会被多个子表反复探测,
    18 万行级大表上避免重复全表扫描。

    缓存无失效机制是安全的:本脚本为一次性 CLI(由 server 以子进程调用),
    连接以 `mode=ro` 只读打开,全程无 INSERT/UPDATE/DDL —— 进程存续期内
    表数据与结构不可能变化,不存在读到过期结果的路径。
    容量按 (表,列) 天然受 schema 规模约束,再加硬上限兜底极端宽表库。"""
    if pk_of.get(t) == c: return True
    k = (t, c)
    if k in _uniq_cache: return _uniq_cache[k]
    try:
        r = con.execute(f'SELECT COUNT(*) n, COUNT(DISTINCT {qi(c)}) d FROM {qi(t)}').fetchone()
        out = r[0] > 0 and r[0] == r[1]
    except Exception:
        out = False
    if len(_uniq_cache) >= _UNIQ_CACHE_MAX: _uniq_cache.clear()
    _uniq_cache[k] = out
    return out

def parent_key(pt, child_col, stem):
    """父表连接键:声明PK优先;无PK时(如上传CSV建表全无PK)退而用同名列/词干_id·_code/id 列。
    不再因缺 PK 直接跳过——否则上传数据的取值重叠关系发现会全部静默失效。"""
    if pk_of.get(pt): return pk_of[pt]
    pcols = [c for c, _ in cols_of[pt]]
    lower = {c.lower(): c for c in pcols}
    for cand in (child_col.lower(), f"{stem}_id", f"{stem}_code", "id", f"{pt.lower()}_id"):
        if cand in lower: return lower[cand]
    for c in pcols:                         # 兜底:父表任一 *_id/*_code 列
        if re.search(r"_(id|code)$", c, re.I): return c
    return None

seen = {(l["source_concept"], l["target_concept"]) for l in links}
for t in tabs:
    for c, _ in cols_of[t]:
        m = re.match(r"(.+?)_(id|code)$", c, re.I)
        if m:
            stem = m.group(1).lower()
        elif any(c.lower() == t2.lower() for t2 in tabs if t2 != t):
            stem = c.lower()                            # 无后缀键名(列名==某表名):Burr-micro 实证零精度代价
        elif any(len(t2) >= 4 and c.lower().startswith(t2.lower()) and c.lower()[len(t2):].isdigit()
                 for t2 in tabs if t2 != t):
            stem = next(t2.lower() for t2 in tabs if t2 != t and len(t2) >= 4
                        and c.lower().startswith(t2.lower()) and c.lower()[len(t2):].isdigit())   # 前缀键名 Country1→Country(DR-011)
        else:
            continue
        for pt in tabs:
            if pt == t or (t, pt) in seen: continue
            if stem not in pt.lower(): continue
            pk = parent_key(pt, c, stem)
            if not pk: continue
            child, parent = distinct(t, c), distinct(pt, pk)
            if not child: continue
            ov = 100.0 * len(child & parent) / len(child)
            # 仅当父键为候选键(唯一)且重叠≥60% 才判 verified;否则即便重叠高也只作 candidate
            if ov >= 60 and is_key_unique(pt, pk):
                links.append({"source_concept": t, "target_concept": pt, "verb": "关联",
                              "status": "verified", "overlap": round(ov, 1), "note": f"{c}→{pt}.{pk} 重叠{ov:.0f}%·父键唯一",
                              "founded_relation": "relatedToAtSomeTime", "temporal": "atSomeTime"})
                seen.add((t, pt)); seen.add((pt, t))   # 反向也记,避免 A→B 与 B→A 双向冗余边
            elif ov >= 20:
                links.append({"source_concept": t, "target_concept": pt, "verb": "关联",
                              "status": "candidate", "overlap": round(ov, 1), "note": "弱重叠,送审",
                              "founded_relation": "relatedToAtSomeTime", "temporal": "atSomeTime"})
                seen.add((t, pt)); seen.add((pt, t))
print(f"[quick_build] 关系 {len(links)} 条 (verified {sum(1 for l in links if l['status']=='verified')})", flush=True)

ir = {"scenario": {"name": name, "style": "quick_build(数据驱动)", "object_count": len(objects), "relation_count": len(links)},
      "objects": objects, "relations": links}
with open(out, "w") as _fp: json.dump(ir, _fp, ensure_ascii=False, indent=1)
print(f"[quick_build] 完成 → {out}", flush=True)
