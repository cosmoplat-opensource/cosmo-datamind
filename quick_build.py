#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""quick_build.py <sqlite.db> <out_ir.json> <图谱名>
数据驱动快速建本体(沿用平台反幻觉规则):表→对象;关系=声明FK ∪ (命名配对+取值重叠≥60%→verified,否则candidate)。
产物为 DataMind 图谱 IR,可直接在 UI 可视化;深加工可再走 ontology-build / gov-app-ontology-build 技能。

结构(IR-007/DR-045):CLI/构建逻辑收进 build() + `if __name__=="__main__"` 守卫,
纯函数(key_stem/key_name_ok)与 build() 均可被测试/编程调用;server 子进程调用行为不变。"""
import json, re, sqlite3, sys
from collections import OrderedDict as _OrderedDict
import dao_core   # DR-035:裁决决策与命名/重叠原语的单一事实源

qi = lambda s: '"' + str(s).replace('"', '""') + '"'   # 安全转义 SQL 标识符(列名/表名含引号也不破格)
_KIND_BFO = {"object": "MaterialEntity", "event": "Process"}   # kind→BFO 上层范畴(IOF 借鉴)

# 模块级可变状态:由 build() 填充,供 DB 相关 helper(distinct/is_key_unique/parent_key)闭包引用。
con = None
cols_of, pk_of = {}, {}


def distinct(t, c, cap=20000):
    try:
        return set(r[0] for r in con.execute(f'SELECT DISTINCT {qi(c)} FROM {qi(t)} LIMIT {cap}') if r[0] not in (None, ""))
    except Exception: return set()

_UNIQ_CACHE_MAX = 4096          # 容量上限:超出按 LRU 逐出最久未用项
_uniq_cache = _OrderedDict()
def is_key_unique(t, c):
    """父连接键须为候选键(值唯一)才构成真 FK。

    声明 PK 短路不查库;COUNT 结果按 (表,列) 缓存——同一父键会被多个子表反复探测,
    18 万行级大表上避免重复全表扫描。

    缓存无失效机制是安全的:本脚本为一次性 CLI(由 server 以子进程调用),
    连接以 `mode=ro` 只读打开,全程无 INSERT/UPDATE/DDL —— 进程存续期内
    表数据与结构不可能变化,不存在读到过期结果的路径。
    容量按 (表,列) 天然受 schema 规模约束;上限兜底极端宽表库,
    满时按 LRU 逐出单个最久未用项(而非整体清空):同一父键会被多个子表连续探测,
    命中即刷新为最近使用,热点键因此不会被逐出重建。"""
    if pk_of.get(t) == c: return True
    k = (t, c)
    if k in _uniq_cache:
        _uniq_cache.move_to_end(k)           # 命中即刷新为最近使用 —— 这才是 LRU
        return _uniq_cache[k]
    try:
        r = con.execute(f'SELECT COUNT(*) n, COUNT(DISTINCT {qi(c)}) d FROM {qi(t)}').fetchone()
        out = r[0] > 0 and r[0] == r[1]
    except Exception:
        out = False
    if len(_uniq_cache) >= _UNIQ_CACHE_MAX:
        _uniq_cache.popitem(last=False)      # 逐出最久未用项,避免整体清空造成的反复重建
    _uniq_cache[k] = out
    return out

# 命名校验/词根原语统一收敛到 dao_core(DR-035),此处再导出保持既有引用不破。
key_stem = dao_core.key_stem
key_name_ok = dao_core.key_name_ok


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


def build(db, out, name):
    """数据驱动构建一张图谱 IR,写入 out 并返回 ir(供测试/编程调用)。"""
    global con, cols_of, pk_of
    # 只读打开(mode=ro):建本体只取数、绝不改源库;缺库时显式报错而非静默新建空库
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True); con.row_factory = sqlite3.Row
    tabs = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    print(f"[quick_build] {len(tabs)} 张表", flush=True)

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
                          "evidence": {"child_key": fk[3], "parent_key": fk[4], "source": "declared_fk"},
                          "founded_relation": "relatedToAtSomeTime", "temporal": "atSomeTime"})

    seen = {(l["source_concept"], l["target_concept"]) for l in links}
    for t in tabs:
        for c, _ in cols_of[t]:
            roles = dao_core.role_targets(c)                 # DR-036 自引用/角色键
            m = re.match(r"(.+?)_(id|code)$", c, re.I)
            if m:
                stem = m.group(1).lower()
            elif any(c.lower() == t2.lower() for t2 in tabs if t2 != t):
                stem = c.lower()                            # 无后缀键名(列名==某表名):Burr-micro 实证零精度代价
            elif any(len(t2) >= 4 and c.lower().startswith(t2.lower()) and c.lower()[len(t2):].isdigit()
                     for t2 in tabs if t2 != t):
                stem = next(t2.lower() for t2 in tabs if t2 != t and len(t2) >= 4
                            and c.lower().startswith(t2.lower()) and c.lower()[len(t2):].isdigit())   # 前缀键名 Country1→Country(DR-011)
            elif roles:
                stem = re.sub(r"(_?id|_?code)$", "", dao_core._snake(c)).strip("_")   # 角色键词根(reports_to 等无后缀)
            else:
                continue
            for pt in tabs:
                if (t, pt) in seen: continue
                self_ref = (pt == t)
                ptl = pt.lower()
                if self_ref:
                    if "self" not in roles: continue        # 自引用仅对含 self 的角色键开放(DR-036)
                else:
                    genus_hit = any(g != "self" and g in ptl for g in roles)   # 角色属类词命中父表名
                    if not (stem in ptl or genus_hit): continue   # 非角色键沿用原表名子串校验,不广泛放宽
                pk = parent_key(pt, c, stem)
                if not pk: continue
                if self_ref and pk == c: continue           # 自引用键不能指向自己这一列
                child, parent = distinct(t, c), distinct(pt, pk)
                if not child: continue
                ov = dao_core.overlap_pct(child, parent)
                # 父键唯一度仅在 ov≥60 时探测(保留短路,避免弱重叠也全表 COUNT);
                # 决策统一走 dao_core.classify 的 compat 口径(min_distinct=1、不排除PK作子键)——
                # 与 quick_build 历史行为逐值等价(非角色键),漂移就此收敛到单一裁决核(DR-035)。
                punique = is_key_unique(pt, pk) if ov >= 60 else False
                # DR-037 接线:子键唯一而父键不唯一 → 方向反了(真方向 pt→t)。
                # 抑制这条反向边、且不污染 seen——让正向在处理多侧表(pt)的该列时自然发现。
                if ov >= 60 and not self_ref and dao_core.should_reverse(is_key_unique(t, c), punique):
                    continue
                verdict = dao_core.classify(overlap=ov, parent_unique=punique,
                                            name_ok=dao_core.name_ok(c, pt, pk, child_table=t),
                                            child_distinct=len(child),
                                            min_distinct=1, exclude_pk_child=False)
                st = verdict["status"]
                if st == "drop": continue
                if verdict.get("name_mismatch"):
                    note = (f"{c}→{pt}.{pk} 重叠{ov:.0f}% 但键名词根不一致"
                            f"({key_stem(c)}≠{key_stem(pk)}),疑为自增键值域巧合,送审")
                    ev = {"child_key": c, "parent_key": pk, "overlap": round(ov, 1),
                          "source": "key_overlap", "name_mismatch": True}
                elif st == "verified":
                    note = f"{c}→{pt}.{pk} 重叠{ov:.0f}%·父键唯一"
                    ev = {"child_key": c, "parent_key": pk, "overlap": round(ov, 1), "source": "key_overlap"}
                else:   # 弱重叠 candidate
                    note = "弱重叠,送审"
                    ev = {"child_key": c, "parent_key": pk, "overlap": round(ov, 1), "source": "key_overlap"}
                link = {"source_concept": t, "target_concept": pt, "verb": "关联",
                        "status": st, "overlap": round(ov, 1), "note": note, "evidence": ev,
                        "founded_relation": "relatedToAtSomeTime", "temporal": "atSomeTime"}
                if self_ref:                                # DR-036:有意的层级自引用,语义化并标记
                    link["verb"] = "上级"
                    link["self_ref"] = True
                    ev["self_ref"] = True
                links.append(link)
                seen.add((t, pt)); seen.add((pt, t))
    print(f"[quick_build] 关系 {len(links)} 条 (verified {sum(1 for l in links if l['status']=='verified')})", flush=True)

    ir = {"scenario": {"name": name, "style": "quick_build(数据驱动)", "object_count": len(objects), "relation_count": len(links)},
          "objects": objects, "relations": links}
    with open(out, "w") as _fp: json.dump(ir, _fp, ensure_ascii=False, indent=1)
    print(f"[quick_build] 完成 → {out}", flush=True)
    return ir


if __name__ == "__main__":
    build(sys.argv[1], sys.argv[2], sys.argv[3])
