#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把行级裁决实测结果(data/chuangzhi/row_adjudication_results.json)回写全量概念 IR。

功能:
  1. verified 边 upsert(带成员表/键/重叠率/切片坐标/重放库的完整证据);
  2. 命名门 candidate 边附加行级裁决尝试证据(保持 candidate,待人审);
  3. 仅共享组织/审计通用列的边标注 evidence_class=generic_columns(行级验证为
     重言式,不升级);同实体副本边标注 duplicate_variant(建议人审合并)。
用法: python3 scripts/cz_apply_row_adjudication.py <ir.json> [results.json]
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

GENERIC = {'company_code', 'corporation_code', 'base_unit_code', 'base_unit_id', 'currency_code',
           'factory_code', 'product_factory_code', 'creator_id', 'last_editor_id'}


def apply(ir, results):
    sl = results["coordinate"]
    replay = results["replay_db"]
    upgraded = inserted = 0
    for s, t, ck, pk, ov, ptable, ctable, cd in results["verified_edges"]:
        hit = next((r for r in ir["relations"]
                    if {r["source_concept"], r["target_concept"]} == {s, t}), None)
        edge = {"source_concept": s, "target_concept": t, "verb": "关联",
                "status": "verified", "evidence_status": "verified",
                "semantic": "not_reviewed", "semantic_status": "not_reviewed",
                "overlap": ov,
                "note": f"概念级行级裁决:成员表 {ctable}.{ck} → {ptable}.{pk} 重叠 {ov}%·父键唯一·命名有据",
                "evidence": {"source": "row_adjudication",
                             "child_concept": s, "parent_concept": t,
                             "child_table": ctable, "parent_table": ptable,
                             "child_key": ck, "parent_key": pk,
                             "overlap": ov, "parent_unique": True, "name_ok": True,
                             "evidence_complete": True,
                             "evidence_method": "sql_distinct_intersect",
                             "child_distinct": cd, "slice": sl, "replay_db": replay,
                             "coordinate": "cq"},
                "founded_relation": "", "grounding_iri": "", "grounding_status": "unmapped",
                "grounding_reason": "行级裁决证明数据连接成立,BFO/IOF 关系语义仍待映射",
                "temporal": ""}
        if hit:
            hit.update(edge)
            upgraded += 1
        else:
            ir["relations"].append(edge)
            inserted += 1
    for s, t, ck, pk, ov, ptable, ctable, cd, why in results["candidate_edges"]:
        hit = next((r for r in ir["relations"]
                    if {r["source_concept"], r["target_concept"]} == {s, t}), None)
        if hit:
            hit.setdefault("evidence", {}).update(
                {"row_adjudication": {"overlap": ov, "parent_unique": True, "name_ok": False,
                                      "child_table": ctable, "parent_table": ptable,
                                      "child_key": ck, "parent_key": pk, "note": why}})
    n_gen = n_var = 0
    for r in ir["relations"]:
        if r.get("status") == "verified":
            continue
        sh = set((r.get("evidence") or {}).get("shared_columns", []))
        pair_names = r["source_concept"] + "|" + r["target_concept"]
        ev = r.setdefault("evidence", {})
        if ev.get("evidence_class"):
            continue                             # 幂等:已标注的边不重复追加
        if sh & GENERIC:
            ev["evidence_class"] = "generic_columns"
            r["note"] = (r.get("note") or "") + ";共享列属组织/审计通用列,不构成引用证据,行级验证将为重言式,保持 candidate"
            n_gen += 1
        elif ("co_item" in r["source_concept"] and "co_item" not in r["target_concept"]
              and ("jz_t_co_item" in r["target_concept"] or "itemlzf" in r["target_concept"])):
            ev["evidence_class"] = "duplicate_variant"
            r["note"] = (r.get("note") or "") + ";同一实体的区域/技术副本,键域一致性建议人审后合并概念"
            n_var += 1
    return upgraded, inserted, n_gen, n_var


if __name__ == "__main__":
    ir_path = sys.argv[1]
    res_path = sys.argv[2] if len(sys.argv) > 2 else "data/chuangzhi/row_adjudication_results.json"
    ir = json.load(open(ir_path, encoding="utf-8"))
    results = json.load(open(res_path, encoding="utf-8"))
    upgraded, inserted, n_gen, n_var = apply(ir, results)
    try:
        import build_quality
        ir["build_quality"] = build_quality.evaluate(ir)
        ir["gaps"] = ir["build_quality"]["gaps"]
    except ImportError:
        pass
    ir["scenario"]["relation_count"] = len(ir["relations"])
    ir["scenario"]["row_adjudication"] = {
        "verified_upgraded": upgraded, "verified_inserted": inserted,
        "generic_annotated": n_gen, "variant_annotated": n_var,
        "contract": results["contract"], "coordinate": results["coordinate"]}
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=os.path.dirname(os.path.abspath(ir_path)),
                                         prefix=".czra-", suffix=".json", delete=False) as fp:
            tmp = fp.name
            json.dump(ir, fp, ensure_ascii=False, indent=1, allow_nan=False)
        os.replace(tmp, ir_path)
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)
    from collections import Counter
    print("关系状态分布:", dict(Counter(r["status"] for r in ir["relations"])))
    print(f"升级 {upgraded} · 新增 {inserted} · 通用列标注 {n_gen} · 变体标注 {n_var} · "
          f"验收 {ir.get('build_quality', {}).get('result')}")
