#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""反幻觉评测台 —— IR-009 / DR-039。

把「LLM 提议、数据裁决」的反造假主张变成**可回归的数字**:喂一批带真值标签的
候选关系(真外键 + 植入的假边),量化裁决器 verify 真的、拒假的能力——
精确率 / 召回 / F1 + **幻觉泄漏率**(假边被判 verified 的比例,越低越强)。

纯确定性、离线;裁决口径与 quick_build 完全一致(同走 dao_core 单一裁决核):
  重叠率 + 父键唯一 + 命名闸(dao_core.name_ok)+ 方向测试(should_reverse 抑制)。
故本台测的正是**生产构建实际用的那道防线**,不是另造一套。
"""
import sqlite3
import dao_core

_qi = lambda s: '"' + str(s).replace('"', '""') + '"'


def _signals(con, table, col):
    """从库里取一列的 distinct 集合与唯一性(与 quick_build 同口径:剔空)。"""
    try:
        vals = [r[0] for r in con.execute(f'SELECT {_qi(col)} FROM {_qi(table)}')]
    except Exception:
        return set(), 0, False
    dset, n = dao_core.clean(vals)
    return dset, n, (n > 0 and len(dset) == n)


def adjudicate(con, cand, theta=dao_core.MIN_OVERLAP, min_distinct=1, low_card_floor=0, adaptive=False):
    """对一条候选关系做裁决,返回 {verified, status, overlap, name_ok, ...}。
    与 quick_build 同链:方向抑制 → 命名闸 → classify。
    theta/min_distinct/low_card_floor 可调,供 DR-038 自适应门槛对比实验;
    adaptive=True 时 θ 由 dao_core.adaptive_theta(子键 distinct) 逐候选计算。"""
    ct, cc = cand["child_table"], cand["child_col"]
    pt, pc = cand["parent_table"], cand["parent_col"]
    cset, _, cuniq = _signals(con, ct, cc)
    pset, _, puniq = _signals(con, pt, pc)
    cdist = len(cset)
    if adaptive:
        theta = dao_core.adaptive_theta(cdist)
    ov = dao_core.overlap_pct(cset, pset)
    # 方向测试:子键唯一而父键不唯一 → 方向反,抑制(与 quick_build DR-037 接线一致)
    if ov >= theta and dao_core.should_reverse(cuniq, puniq):
        return {"verified": False, "status": "reverse_suppressed", "overlap": ov, "name_ok": None}
    name_ok = dao_core.name_ok(cc, pt, pc, child_table=ct)
    v = dao_core.classify(overlap=ov, parent_unique=puniq, name_ok=name_ok,
                          child_distinct=cdist, theta=theta,
                          min_distinct=min_distinct, exclude_pk_child=False)
    st = v["status"]
    # DR-038 低基数护栏(可选):verified 但子键 distinct 过少 → 降级 candidate(不丢,送审)
    if low_card_floor and st == "verified" and cdist < low_card_floor:
        st = "candidate"
    return {"verified": st == "verified", "status": st, "overlap": round(ov, 1),
            "name_ok": name_ok, "child_distinct": cdist}


def evaluate(db_path, candidates, **kw):
    """在库上对全部带标签候选评测,返回混淆矩阵 + 精确率/召回/F1 + 幻觉泄漏率 + 逐条 rows。"""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    tp = fp = fn = tn = 0
    rows = []
    for c in candidates:
        res = adjudicate(con, c, **kw)
        pred, truth = res["verified"], c["is_true_fk"]
        if truth and pred:
            tp += 1
        elif truth and not pred:
            fn += 1
        elif (not truth) and pred:
            fp += 1
        else:
            tn += 1
        rows.append({**c, **res})
    con.close()
    prec = tp / (tp + fp) if (tp + fp) else 1.0
    rec = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    leak = fp / (fp + tn) if (fp + tn) else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": round(prec, 3), "recall": round(rec, 3), "f1": round(f1, 3),
            "hallucination_leak_rate": round(leak, 3), "rows": rows}
