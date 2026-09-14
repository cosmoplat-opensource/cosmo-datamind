#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""创智湖仓全量实体规范化构建:2,206 表 → ≤300 概念对象(确定性,不调 LLM)。

回应的方法论缺口:此前 `表→对象` 是恒等映射,对象数完全跟随输入表数
(108 表出 108 对象 / 采样 6 表出 6 对象)。本脚本在对象层之前加实体规范化:

  1. 表名归一 → (层, 来源, 业务词根):交错剥层前缀/厂码/源前缀/wms_epg 站点/
     噪声后缀(offline/bak/时间戳/test/tmp)与尾 token 数字变体(pcb1205→pcb);
  2. 同名词根族 = 一个概念(跨厂/跨层同名实体,与实体矩阵同口径);
     跨厂列分歧不拆族,仅统计分歧度(divergent_families);
  3. 概念数压缩:同词头/前缀包含 + 列 Jaccard≥0.15 归并;单表概念列相似 ≥0.30
     吸收、<0.05 且无共享列移入排除清单(覆盖率不变式:收编+排除=全量表数);
  4. 角色分类:事件词根→event,主数据词根→object,其余(记录/单据/汇总)→ice;
     cn 取《C 文档/A 文档》业务栏的有据中文映射,未映射词根保留英文键;
  5. 概念级候选关系按「共享业务列≥4」取证(schema 口径,一律 candidate);
     行级裁决由 scripts/cz_apply_row_adjudication.py 以实测结果升级。

用法: python3 scripts/cz_canonical_build.py <mart.db> <out_ir.json> <图名> [max_objects]
"""
import json
import os
import re
import sqlite3
import sys
import tempfile
from collections import defaultdict

# 信封字段 = 不具备实体区分度的公共/审计列。ca_id 是区域/工位主键(仅 62 表携带,
# 2.8%),属业务键,不在其列;实测依据见 cz_envelope 覆盖率表。
ENVELOPE = {
    "id", "dt", "insert_time", "create_time", "create_user", "edit_time", "edit_user",
    "dept_id", "data_auth", "tenement_id", "data_source", "data_source_dc",
    "sap_factory_code", "del_flag_dc", "unique_id", "insert_dc_time", "vcode",
    "tenement_common_code",
}
NOISE_SUFFIX = re.compile(r"_(offline|bak\d*|bak_\d+|20\d{6}|\d{8}|test\d*|temp\d*|tmp|copy\d*|lzf|new|old|cf|v\d+)$")
FACTORIES = {"cq", "dx", "hf", "qd", "sd", "ss", "tg1", "tg2", "tg", "tj", "oem", "sjyy", "wms", "orw", "mpm", "csmp", "szmf", "vdms", "cgpt", "vmi", "mes", "gp", "sqm", "cqm", "idss", "cap", "user_center", "device_ai", "capacity"}
SOURCES = ("t", "tb", "sjyy", "wms", "sy", "vmi", "mes", "epg")
SITE_STOP = {"ods", "wms", "raw", "t", "tb", "sy"}
GENERIC_TAIL = re.compile(r"_(detail|info|base|list|data|day|month|items|record|records|result_list)$")
DIM_HINT = re.compile(r"(^|_)(co|dict|dim|sy)_(item|area|group|dict|user|dept|factory|supplier|customer|material)|^(equipment|warehouse|route|bom|operation|work_center|workshop|team|department|region|shift)")
EVENT_HINT = re.compile(r"(event|alarm|incident|downtime|error)")

# 有据中文名映射:词条取自《C_chaungzhi_字段级分析.md》与《A_chaungzhi生产表清单说明.md》
# 的业务栏用词,未收录词根保留英文键(不臆造)。
CN_MAP = {
    "wip_detail": "在制品过站明细", "wip": "在制品过站", "wip_item_trace": "在制品单件追溯",
    "wip_station_item": "在制品工位过站", "wip_error": "过站不良记录", "wip_repair": "返修记录",
    "wip_pcb_item": "在制品PCB板", "wip_tracking": "在制品追踪",
    "pm_mo": "生产工单", "pm_project": "生产项目", "pm_order_out": "工单完工出库",
    "pm_order": "生产订单", "pm_order_in": "工单完工入库",
    "co_item": "物料主数据", "co_group": "工序分组", "co_area": "区域工位",
    "co_route": "工艺路线", "co_bom": "物料清单", "co_supplier": "供应商",
    "co_device": "设备", "co_operation": "工序", "co_sn_relation": "SN关联关系",
    "dict": "数据字典", "sy_dict_val": "数据字典值",
    "product_connect_log": "产品衔接日志", "double_code_log": "双码日志",
    "fct_factor": "功能测试因子", "smtaoi_factor": "贴片后AOI检测因子",
    "aoi_factor": "AOI检测因子", "ict_factor": "在线测试(ICT)因子",
    "dipaoi_factor": "插件后AOI检测因子", "pack_sn": "包装序列号",
    "order_fpy_ok_detail": "订单直通率明细", "qr_code": "产品二维码",
    "order_receive_pallet": "收货托盘", "pallet_barcode": "托盘条码",
    "ods_raw_order_in": "仓储原始入库单", "ods_raw_order_out": "仓储原始出库单",
    "raw_pallet_barcode_sn": "原始托盘条码SN", "books": "质量台账",
    "smt_tp_point_static": "SMT测点统计", "machine_down_time_rate": "设备停机率",
    "smt_theo_eff": "SMT理论效率", "fpy": "直通率",
}
_FAILED = 0


def parse_name(table):
    """表名 → (layer, source, core)。core 为业务词根;cluster_key 再剥泛化尾。"""
    global _FAILED
    t = table.lower()
    layer = ""
    m = re.match(r"^(ods|stg|dwd|dws|dim|ads|agg)_(tg1_|tg2_)?", t)
    if m:
        layer = m.group(1)
        t = t[m.end():]
    toks = t.split("_")
    source = []
    # 交错消费:厂码与源前缀可任意嵌套(cq_t_* / qd_tb_* / sjyy_t_*),单循环吃干净
    while toks and (toks[0] in FACTORIES or toks[0] in SOURCES or toks[0].isdigit()):
        if toks[0] == "epg" and len(toks) > 1:
            toks = toks[1:]                       # epg 后跟站点标识(hfzn_5020/cqygc/8787),吃到业务词为止
            site = 0
            while (toks and site < 2 and toks[0] not in SITE_STOP
                   and toks[0] not in FACTORIES and toks[0] not in SOURCES):
                source.append(toks[0])
                toks = toks[1:]
                site += 1
            continue
        source.append(toks[0])
        toks = toks[1:]
    core = "_".join(toks) or table.lower()
    prev = None
    while prev != core:
        prev = core
        core = NOISE_SUFFIX.sub("", core)
        core = re.sub(r"_\d+$", "", core)
        m2 = re.match(r"^(.*?_)([a-z]+)(\d{3,4})$", core)   # 尾 token 尾部数字变体(pcb1205→pcb)
        if m2 and len(m2.group(2)) >= 3:
            core = m2.group(1) + m2.group(2)
    if len(core.replace("_", "")) < 2:
        _FAILED += 1
        core = table.lower()
    return layer, "_".join(source), core


def cluster_key(core):
    """泛化尾剥离后的聚类键:同实体的 detail/info/base/list 变体归为一族。"""
    k = GENERIC_TAIL.sub("", core)
    k = re.sub(r"_(day|month)$", "", k)
    return k or core


def business_cols(columns):
    return {c.lower() for c in columns if c.lower() not in ENVELOPE}


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def build(mart_db, out_ir, name, max_objects=300):
    con = sqlite3.connect(f"file:{mart_db}?mode=ro", uri=True)
    tables = {r[0]: {"layer": r[1], "source": r[2] or "", "num_cols": r[3],
                     "meta_rows": r[4], "is_production": r[5]}
              for r in con.execute("SELECT table_id, layer_id, source, num_cols, meta_rows, is_production FROM cz_tables")}
    cols = defaultdict(set)
    for t, c in con.execute("SELECT table_id, col_name FROM cz_columns"):
        cols[t].add(c.lower())
    con.close()

    # ① 显式排除:非生产表(test/temp/bak/演示),逐表给原因
    excluded = []
    for t, meta in sorted(tables.items()):
        if not meta["is_production"]:
            tl = t.lower()
            reason = ("test 表" if "test" in tl else
                      "temp 表" if "temp" in tl else
                      "bak 备份" if "bak" in tl else "非生产表")
            excluded.append({"table": t, "reason": reason})
    prod = [t for t, m in tables.items() if m["is_production"]]

    # ② 词根聚类
    groups = defaultdict(list)
    parsed = {}
    for t in prod:
        layer, source, core = parse_name(t)
        parsed[t] = (layer, source, core)
        groups[cluster_key(core)].append(t)

    # ③ 同名词根族 = 一个概念;跨厂列分歧仅统计不拆族
    concepts = []
    divergent = 0
    for key, members in groups.items():
        members = sorted(members)
        layers = sorted({parsed[t][0] for t in members if parsed[t][0]})
        sources = sorted({parsed[t][1] for t in members if parsed[t][1]})
        sigs = {t: business_cols(cols.get(t, set())) for t in members}
        csig = set().union(*sigs.values()) if sigs else set()
        if len(members) >= 3:
            avg_j = sum(jaccard(sigs[t], csig) for t in members) / len(members)
            if avg_j < 0.30:
                divergent += 1
        concepts.append({"key": key, "members": members, "layers": layers,
                         "sources": sources, "cols": csig,
                         "meta_rows": max((tables[t]["meta_rows"] or 0) for t in members)})
    concepts.sort(key=lambda c: (-c["meta_rows"], c["key"]))

    # ④ 概念数压缩(确定性):
    #    a) 同词头/前缀包含(短键 ≥2 token)+ 列 Jaccard≥0.15 的最小对归并;
    #    b) 单表概念:并入列相似 ≥0.30 的邻居;<0.05 且无共享列移入排除清单。
    def related(a, b):
        ka, kb = a["key"], b["key"]
        if ka.split("_")[0] == kb.split("_")[0]:
            return True
        shorter = ka if len(ka) <= len(kb) else kb       # 前缀包含:短键须 ≥2 个 token
        return ((ka.startswith(kb) or kb.startswith(ka))
                and len(shorter.split("_")) >= 2)

    extra_merged = 0
    while len(concepts) > max_objects:
        best = None
        for i, a in enumerate(concepts):
            if len(a["members"]) == 1:
                continue                                 # 单表概念留给吸收/排除环节
            for j, b in enumerate(concepts):
                if i == j or not related(a, b):
                    continue
                jv = jaccard(a["cols"], b["cols"])
                if jv >= 0.15 and (best is None or jv > best[0]):
                    best = (jv, i, j)
        if best is None:
            break
        _jv, i, j = best
        a, b = concepts[i], concepts[j]
        keep, drop = (a, b) if len(a["members"]) >= len(b["members"]) else (b, a)
        keep["members"] = sorted(set(keep["members"]) | set(drop["members"]))
        keep["layers"] = sorted(set(keep["layers"]) | set(drop["layers"]))
        keep["sources"] = sorted(set(keep["sources"]) | set(drop["sources"]))
        keep["cols"] |= drop["cols"]
        keep["meta_rows"] = max(keep["meta_rows"], drop["meta_rows"])
        if len(drop["key"]) < len(keep["key"]):
            keep["key"] = drop["key"]
        concepts.remove(drop)
        extra_merged += 1

    absorbed_singletons = excluded_singles = 0
    for s in [c for c in concepts if len(c["members"]) == 1]:
        if len(concepts) <= max_objects:
            break
        best, best_j = None, 0.0
        for c in concepts:
            if c is s or len(c["members"]) == 1:
                continue
            jv = jaccard(s["cols"], c["cols"])
            if jv > best_j:
                best, best_j = c, jv
        if best is not None and best_j >= 0.30:
            best["members"] = sorted(set(best["members"]) | set(s["members"]))
            best["cols"] |= s["cols"]
            best["meta_rows"] = max(best["meta_rows"], s["meta_rows"])
            concepts.remove(s)
            absorbed_singletons += 1
        elif best is not None and best_j < 0.05:
            excluded.append({"table": s["members"][0],
                             "reason": f"孤立表(词根 {s['key']},与其它表无共享业务列),未入对象"})
            concepts.remove(s)
            excluded_singles += 1

    # ⑤ 组装 IR:对象绑成员表;概念级候选关系按共享业务列 ≥4 取证
    def kind_of(key):
        if EVENT_HINT.search(key):
            return "event"
        if DIM_HINT.search(key):
            return "object"
        return "ice"

    objects = []
    by_concept = {}
    for cpt in concepts:
        nm = "cz_" + re.sub(r"[^a-z0-9]+", "_", cpt["key"]).strip("_")[:48]
        base, k = nm, 2
        while nm in by_concept:
            nm = f"{base}_{k}"
            k += 1
        first = cpt["members"][0]
        attrs = [{"col": c, "cn": "", "type": ""}
                 for c in sorted(business_cols(cols.get(first, set())))[:15]]
        objects.append({"name": nm, "cn": CN_MAP.get(cpt["key"], cpt["key"]),
                        "kind": kind_of(cpt["key"]),
                        "table": first, "tables": cpt["members"],
                        "field_count": len(cpt["cols"]), "attrs": attrs,
                        "candidate": False, "indicators": [],
                        "member_tables": len(cpt["members"]),
                        "layers": cpt["layers"], "sources": cpt["sources"][:8],
                        "remark": f"实体规范化:收编 {len(cpt['members'])} 张表 (词根 {cpt['key']})",
                        "bfo": "", "definition": "", "isPrimitive": True,
                        "example": "", "counterExample": "", "maturity": "Provisional",
                        "provenance": {"directSource": first, "adaptedFrom": [], "excerptedFrom": None}})
        by_concept[nm] = cpt

    scored = []
    names = list(by_concept)
    for i, sa in enumerate(names):
        for sb in names[i + 1:]:
            shared = by_concept[sa]["cols"] & by_concept[sb]["cols"]
            if len(shared) >= 4:
                scored.append((len(shared), sa, sb, sorted(shared)))
    scored.sort(key=lambda x: -x[0])
    rels, seen_pair = [], set()
    for shared_n, sa, sb, shared in scored[:400]:
        if (sa, sb) in seen_pair:
            continue
        seen_pair.add((sa, sb))
        rels.append({"source_concept": sa, "target_concept": sb, "verb": "关联",
                     "status": "candidate", "evidence_status": "candidate",
                     "semantic": "not_reviewed", "semantic_status": "not_reviewed",
                     "overlap": None,
                     "note": f"概念级 schema 候选:成员表共享 {shared_n} 个业务列",
                     "evidence": {"shared_columns": shared[:8], "shared_count": shared_n,
                                  "source": "schema_shared_columns", "direction": "undetermined"},
                     "founded_relation": "", "grounding_iri": "",
                     "grounding_status": "unmapped",
                     "grounding_reason": "schema 级共享列证据不足以确定 BFO/IOF 关系",
                     "temporal": ""})

    covered = sum(len(c["members"]) for c in concepts)
    ir = {"scenario": {"name": name, "style": "canonical(全量实体规范化:2206表→概念对象)",
                       "object_count": len(objects), "relation_count": len(rels),
                       "query_errors": [],
                       "coverage": {"tables_total": len(tables), "absorbed": covered,
                                    "excluded": len(excluded),
                                    "invariant": covered + len(excluded) == len(tables)},
                       "canonicalization": {"divergent_families": divergent,
                                            "extra_merged": extra_merged,
                                            "absorbed_singletons": absorbed_singletons,
                                            "excluded_singletons": excluded_singles,
                                            "fallback_name_count": _FAILED}},
          "objects": objects, "relations": rels,
          "excluded_tables": excluded}
    try:
        import build_quality
        ir["build_quality"] = build_quality.evaluate(ir)
        ir["gaps"] = ir["build_quality"]["gaps"]
    except ImportError:
        pass
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=os.path.dirname(os.path.abspath(out_ir)),
                                         prefix=".czcanon-", suffix=".json", delete=False) as fp:
            tmp = fp.name
            json.dump(ir, fp, ensure_ascii=False, indent=1, allow_nan=False)
        os.replace(tmp, out_ir)
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)
    print(f"[cz_canonical] 生产表 {len(prod)} · 排除 {len(excluded)} · 概念对象 {len(objects)} "
          f"(二次归并 {extra_merged} · 单表吸收 {absorbed_singletons} · 孤立排除 {excluded_singles}) · 概念关系 {len(rels)} 条")
    print(f"[cz_canonical] 覆盖不变式: 收编 {covered} + 排除 {len(excluded)} = {covered + len(excluded)} / {len(tables)}")
    print(f"[cz_canonical] 完成 → {out_ir}")
    return ir


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(__doc__.splitlines()[0], file=sys.stderr)
        sys.exit(2)
    build(sys.argv[1], sys.argv[2], sys.argv[3],
          int(sys.argv[4]) if len(sys.argv) > 4 else 300)
