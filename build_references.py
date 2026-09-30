#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""半自动本体构建的行业参照与本体标准配置。

本模块是构建配置的单一事实源：目录、请求归一、提示词片段、产物注释与确定性
验收都从这里读取同一份定义。它不调用模型、不读外部系统，也不声称 ISA-95/UFO
候选对齐等同于标准符合性认证。
"""
from __future__ import annotations

import copy
import re

import ontology_grounding
import standard_assets


PROFILE_VERSION = 1
MODES = {"reference", "constraint"}


INDUSTRIES = {
    "none": {
        "name": "不选择行业参照",
        "version": "",
        "description": "仅依据当前数据与资料构建，不套用行业概念模板。",
        "concepts": [],
        "relations": [],
    },
    "manufacturing": {
        "name": "制造业",
        "version": "内置参照 1.0",
        "description": "离散制造常见的工单、产品、物料、设备、产线与质量检验概念。",
        "concepts": [
            {"id": "work_order", "name": "生产工单", "required": True,
             "aliases": ["生产工单", "制造工单", "工单", "work_order", "work order"]},
            {"id": "product", "name": "产品", "required": True,
             "aliases": ["产品", "成品", "product", "finished_good"]},
            {"id": "material", "name": "物料", "required": True,
             "aliases": ["物料", "原材料", "零部件", "material", "component", "part"]},
            {"id": "equipment", "name": "设备", "required": True,
             "aliases": ["生产设备", "设备", "机器", "equipment", "machine"]},
            {"id": "quality_inspection", "name": "质量检验", "required": True,
             "aliases": ["质量检验", "质检", "检验记录", "quality_inspection", "inspection"]},
            {"id": "production_line", "name": "生产线",
             "aliases": ["生产线", "产线", "production_line", "line"]},
            {"id": "process_route", "name": "工艺路线",
             "aliases": ["工艺路线", "工艺流程", "routing", "process_route"]},
            {"id": "production_batch", "name": "生产批次",
             "aliases": ["生产批次", "制造批次", "批次", "production_batch", "batch"]},
        ],
        "relations": [
            "生产工单生产产品", "生产工单消耗物料", "设备执行生产工单",
            "生产线包含设备", "质量检验检验产品", "生产工单遵循工艺路线",
        ],
    },
    "chemical": {
        "name": "化工行业",
        "version": "内置参照 1.0",
        "description": "流程化工常见的批次、产品、配方、原料、反应设备与安全监测概念。",
        "concepts": [
            {"id": "production_batch", "name": "生产批次", "required": True,
             "aliases": ["生产批次", "化工批次", "批次", "production_batch", "batch"]},
            {"id": "chemical_product", "name": "化工产品", "required": True,
             "aliases": ["化工产品", "化学品", "chemical_product", "chemical product"]},
            {"id": "formula", "name": "工艺配方", "required": True,
             "aliases": ["工艺配方", "配方", "formula", "recipe"]},
            {"id": "raw_material", "name": "原料", "required": True,
             "aliases": ["化工原料", "原料", "raw_material", "raw material", "feedstock"]},
            {"id": "reactor", "name": "反应釜", "required": True,
             "aliases": ["反应釜", "反应器", "reactor", "vessel"]},
            {"id": "safety_monitoring", "name": "安全监测", "required": True,
             "aliases": ["安全监测", "安全检测", "报警", "safety_monitoring", "alarm"]},
            {"id": "process_parameter", "name": "工艺参数",
             "aliases": ["工艺参数", "温度参数", "压力参数", "process_parameter"]},
            {"id": "storage_tank", "name": "储罐",
             "aliases": ["储罐", "罐区", "storage_tank", "tank"]},
        ],
        "relations": [
            "生产批次生产化工产品", "生产批次遵循工艺配方", "生产批次消耗原料",
            "反应釜承载生产批次", "安全监测监测反应釜", "工艺配方规定工艺参数",
        ],
    },
    "pcba": {
        "name": "PCBA / SMT",
        "version": "内置参照 1.0",
        "description": "PCBA 生产常见的 SMT 工单、产线、板件、贴片、回流与 AOI 检测概念。",
        "concepts": [
            {"id": "smt_work_order", "name": "SMT工单", "required": True,
             "aliases": ["smt工单", "贴片工单", "smt_work_order", "smt order"]},
            {"id": "smt_line", "name": "SMT产线", "required": True,
             "aliases": ["smt产线", "贴片产线", "smt_line"]},
            {"id": "pcba_board", "name": "PCBA板", "required": True,
             "aliases": ["pcba板", "电路板", "pcba_board", "pcb board"]},
            {"id": "placement_machine", "name": "贴片机", "required": True,
             "aliases": ["贴片机", "placement_machine", "pick_and_place"]},
            {"id": "aoi_inspection", "name": "AOI检测", "required": True,
             "aliases": ["aoi检测", "aoi检验", "aoi_inspection"]},
            {"id": "component", "name": "电子元件",
             "aliases": ["电子元件", "元器件", "component", "part"]},
            {"id": "solder_paste", "name": "锡膏",
             "aliases": ["锡膏", "solder_paste", "solder paste"]},
            {"id": "reflow_oven", "name": "回流炉",
             "aliases": ["回流炉", "回流焊", "reflow_oven", "reflow"]},
        ],
        "relations": [
            "SMT工单生产PCBA板", "SMT产线执行SMT工单", "贴片机安装电子元件",
            "回流炉处理PCBA板", "AOI检测检验PCBA板", "SMT工单消耗锡膏",
        ],
    },
    "energy_carbon": {
        "name": "能碳管理",
        "version": "内置参照 1.0",
        "description": "企业能源管理与碳排放核算常见的用能单元、计量器具、测点、能耗、能源绩效参数、"
                       "基准、电价、储能光伏,以及排放源、活动数据、排放因子与温室气体排放量概念。",
        # 概念与关系取自下列标准与公开本体的术语;这里只借用术语做对齐提示,不复制标准正文,
        # 也不声称任何一项的完整符合性。
        "sources": [
            "GB/T 23331-2020 / ISO 50001:2018 能源管理体系(能源使用、能源绩效参数、能源基准)",
            "GB/T 2589-2020 综合能耗计算通则(综合能耗、折标)",
            "GB 17167 用能单位能源计量器具配备和管理通则(计量器具)",
            "GB/T 32150-2025 工业企业温室气体排放核算和报告通则(排放源、活动数据、排放因子;2026-07-01 实施)",
            "ISO 14064-1 / GHG Protocol(范围一/二/三)",
            "ETSI SAREF4ENER V2.1.1(功率曲线、灵活性、储能)",
            "Brick Schema v1.4 / Project Haystack 4(表计、测点)",
            "Open Energy Ontology v2.13.0(基于 BFO 的能源系统本体)",
            "TEC Toolkit ECFO/PECO 1.0.0(排放因子与排放计算溯源)",
        ],
        "concepts": [
            {"id": "energy_use", "name": "用能单元", "required": True, "basis": "ISO 50001 能源使用 / GB/T 23331",
             "aliases": ["用能单元", "用能对象", "用能系统", "用能设备", "能源使用", "energy_unit", "energy use"]},
            {"id": "energy_meter", "name": "能源计量器具", "required": True, "basis": "GB 17167 / Brick Meter",
             "aliases": ["计量器具", "计量表", "表计", "电表", "水表", "气表", "热量表", "仪表", "meter"]},
            {"id": "measurement_point", "name": "测点", "required": True, "basis": "Brick Point / Haystack point / SAREF Measurement",
             "aliases": ["测点", "量测", "点位", "采集点", "measurement", "metric", "point"]},
            {"id": "energy_consumption", "name": "能源消耗量", "required": True, "basis": "GB/T 2589 综合能耗",
             "aliases": ["能源消耗", "能耗", "用能量", "用电量", "综合能耗", "energy_consumption", "consumption"]},
            {"id": "enpi", "name": "能源绩效参数", "basis": "ISO 50001 EnPI",
             "aliases": ["能源绩效参数", "能效指标", "能耗指标", "单位产品能耗", "单耗", "enpi"]},
            {"id": "energy_baseline", "name": "能源基准", "basis": "ISO 50001 EnB",
             "aliases": ["能源基准", "能耗基准", "基准", "baseline", "benchmark", "bench_mark"]},
            {"id": "energy_target", "name": "能源目标与计划", "basis": "ISO 50001 能源目标/指标与行动计划",
             "aliases": ["能耗计划", "用能计划", "能源目标", "能源计划", "energy_plan", "target"]},
            {"id": "energy_tariff", "name": "能源价格", "basis": "SAREF4ENER 激励表 / 分时电价",
             "aliases": ["能源价格", "电价", "分时电价", "峰谷", "价格", "tariff", "price"]},
            {"id": "energy_storage", "name": "储能", "basis": "SAREF4ENER Storage",
             "aliases": ["储能", "储能系统", "电池", "storage", "bess"]},
            {"id": "renewable_generation", "name": "可再生能源发电", "basis": "OEO 发电 / 光伏",
             "aliases": ["光伏", "风电", "发电", "新能源", "pv", "photovoltaic"]},
            {"id": "emission_source", "name": "排放源", "basis": "GB/T 32150-2025 / ISO 14064-1",
             "aliases": ["排放源", "排放设施", "emission_source"]},
            {"id": "activity_data", "name": "活动数据", "basis": "GB/T 32150-2025 / GHG Protocol",
             "aliases": ["活动数据", "活动水平", "activity_data"]},
            {"id": "emission_factor", "name": "排放因子", "basis": "GB/T 32150-2025 / ECFO",
             "aliases": ["排放因子", "碳排放因子", "emission_factor", "conversion_factor"]},
            {"id": "ghg_emission", "name": "温室气体排放量", "basis": "GB/T 32150-2025 / ISO 14064-1",
             "aliases": ["温室气体排放", "碳排放", "排放量", "co2", "carbon_emission", "ghg"]},
        ],
        "relations": [
            "能源计量器具计量用能单元", "测点属于能源计量器具", "能源消耗量归属用能单元",
            "能源绩效参数评价用能单元", "能源绩效参数对照能源基准", "能源目标与计划约束用能单元",
            "能源价格计价能源消耗量", "排放源产生温室气体排放量", "活动数据与排放因子得出温室气体排放量",
            "能源消耗量作为活动数据",
        ],
    },
}


STANDARDS = {
    "none": {
        "name": "不选择本体标准",
        "version": "",
        "description": "保留本地业务概念和关系，不施加上层本体映射。",
        "disclaimer": "",
    },
    "bfo_iof": {
        "name": "BFO 2020 + IOF Core",
        "version": "BFO 2020 / IOF 202602",
        "description": "用 BFO/IOF 类别与官方关系做类型相容性校验；不能安全映射的关系保留在本地命名空间。",
        "disclaimer": "映射检查不等同于 IOF 认证或完整标准符合性证明。",
    },
    "isa95": {
        "name": "ISA-95 / IEC 62264",
        "version": "MESA B2MML V0701",
        "description": "加载本地 MESA B2MML XSD，把设备、物料、人员、工艺段、工单、事件与绩效等概念标为 ISA-95 候选类别。",
        "disclaimer": "B2MML 是 ISA-95 的公开 XML 实现；本仓不复制付费标准正文，也不声称完整 IEC 62264 合规。",
    },
    "ufo": {
        "name": "UFO / OntoUML",
        "version": "gUFO 1.0.0",
        "description": "加载本地 gUFO Turtle，按对象、角色、事件、行动与信息记录的建模种类给出基础类型候选。",
        "disclaimer": "启发式类型标注用于建模复核，不等同于完整 UFO/OntoUML 模型验证。",
    },
}


ISA95_RULES = [
    ("Equipment", ("设备", "机器", "产线", "反应釜", "储罐", "贴片机", "回流炉", "equipment", "machine", "line", "reactor")),
    ("Material", ("产品", "物料", "原料", "元件", "板", "锡膏", "material", "product", "component", "part")),
    ("Personnel", ("人员", "员工", "操作员", "班组", "person", "employee", "operator", "team")),
    ("ProcessSegment", ("工艺", "工序", "流程", "配方", "process", "routing", "formula", "recipe")),
    ("WorkOrder", ("工单", "作业单", "work_order", "work order")),
    ("OperationsEvent", ("事件", "报警", "检验", "检测", "event", "alarm", "inspection")),
    ("OperationsPerformance", ("绩效", "产量", "质量", "能耗", "performance", "yield", "quality")),
]

UFO_KINDS = {
    "object": "KindCandidate",
    "asset": "KindCandidate",
    "role": "RoleCandidate",
    "event": "EventCandidate",
    "action": "ActionEventCandidate",
    "ice": "InformationObjectCandidate",
}


def _public_item(item_id, item):
    value = {key: copy.deepcopy(val) for key, val in item.items()
             if key not in {"concepts", "relations"}}
    value["id"] = item_id
    if "concepts" in item:
        value["concept_count"] = len(item["concepts"])
        value["required_concepts"] = [x["name"] for x in item["concepts"] if x.get("required")]
    return value


def catalog():
    """返回前端可展示的稳定目录；不暴露内部关键词规则。"""
    asset_status = standard_assets.catalog_status()
    standards = []
    for key, value in STANDARDS.items():
        public = _public_item(key, value)
        if key == "none":
            public["asset"] = {"installed": True, "ready": True, "local": True,
                               "file_count": 0, "note": "该选项不需要标准资产"}
        else:
            raw = asset_status.get(key) or {}
            public["asset"] = {field: raw.get(field) for field in (
                "installed", "ready", "local", "file_count", "bytes", "fingerprint",
                "triples", "class_count", "property_count", "term_count", "source",
                "source_revision", "license", "attribution", "errors",
            )}
        standards.append(public)
    return {
        "version": PROFILE_VERSION,
        "modes": [
            {"id": "reference", "name": "参照", "description": "用于提示、候选对齐和未满足项复核，不单独阻断构建。"},
            {"id": "constraint", "name": "强约束", "description": "缺失核心项或违反映射契约时，验收结果为不通过。"},
        ],
        "industries": [_public_item(key, value) for key, value in INDUSTRIES.items()],
        "ontology_standards": standards,
        "default": normalize(None, legacy_default=True),
        "note": "行业模板和标准映射只约束建模过程；没有数据或文档依据时必须报告未满足项，不得为满足模板而编造实体或关系。",
    }


def _choice(value, catalog_map, field, default_id):
    if value is None:
        raw_id, mode = default_id, "reference"
    elif isinstance(value, str):
        raw_id, mode = value, "reference"
    elif isinstance(value, dict):
        raw_id = value.get("id", default_id)
        mode = value.get("mode", "reference")
    else:
        raise ValueError(f"{field} 必须是字符串或对象")
    item_id = str(raw_id or "none").strip().lower()
    mode = str(mode or "reference").strip().lower()
    if item_id not in catalog_map:
        raise ValueError(f"未知的 {field}: {item_id}")
    if mode not in MODES:
        raise ValueError(f"{field}.mode 只能是 reference 或 constraint")
    item = catalog_map[item_id]
    return {
        "id": item_id,
        "name": item["name"],
        "version": item.get("version") or "",
        "mode": "reference" if item_id == "none" else mode,
        "selected": item_id != "none",
    }


def normalize(value, *, legacy_default=False):
    """归一化请求配置。

    ``legacy_default=True`` 仅供旧客户端未发送 references 字段时使用：历史构建器
    总是施加 BFO/IOF，因此继续默认到 BFO/IOF 参照。显式发送 ``none`` 则不会施加。
    """
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("references 必须是对象")
    standard_default = "bfo_iof" if legacy_default else "none"
    industry = _choice(value.get("industry"), INDUSTRIES, "industry", "none")
    standard = _choice(value.get("ontology_standard"), STANDARDS,
                       "ontology_standard", standard_default)
    return {"version": PROFILE_VERSION, "industry": industry,
            "ontology_standard": standard}


def selected_summary(profile):
    profile = normalize(profile)
    parts = []
    for key in ("industry", "ontology_standard"):
        item = profile[key]
        if item["selected"]:
            parts.append(f"{item['name']}（{'强约束' if item['mode'] == 'constraint' else '参照'}）")
    return " · ".join(parts) if parts else "未选择行业或本体标准"


def uses_bfo_iof(profile):
    return normalize(profile)["ontology_standard"]["id"] == "bfo_iof"


def asset_trace(profile):
    """返回所选标准的本地资产版本/指纹，供构建 manifest 与历史追溯。"""
    standard_id = normalize(profile)["ontology_standard"]["id"]
    if standard_id == "none":
        return None
    try:
        return standard_assets.trace(standard_id)
    except Exception as exc:
        return {"id": standard_id, "ready": False, "errors": [str(exc)[:200]]}


def prompt_block(profile):
    """生成进入 LLM 提议阶段的可审计提示片段；无选择时返回明确的空约束说明。"""
    profile = normalize(profile)
    blocks = []
    industry = profile["industry"]
    if industry["selected"]:
        spec = INDUSTRIES[industry["id"]]
        concepts = "、".join(x["name"] for x in spec["concepts"])
        required = "、".join(x["name"] for x in spec["concepts"] if x.get("required"))
        blocks.append(
            f"[行业{'强约束' if industry['mode'] == 'constraint' else '参照'}:{industry['name']} / {industry['version']}]\n"
            f"候选概念:{concepts}\n核心检查项:{required}\n代表关系:{'；'.join(spec['relations'])}\n"
            "这些词只用于归一命名、识别未满足项和比较结构；只有当前库表/文档有依据时才可建成对象或关系。"
            "没有依据的核心项必须保持缺失并在验收中报告，严禁为凑模板编造。"
        )
    standard = profile["ontology_standard"]
    if standard["selected"]:
        spec = STANDARDS[standard["id"]]
        detail = {
            "bfo_iof": "对象按 BFO/IOF 上层类别复核；业务关系只有通过官方名称及定义域/值域检查时才能映射，否则保留为本地关系。",
            "isa95": "优先识别 Equipment、Material、Personnel、ProcessSegment、WorkOrder、OperationsEvent、OperationsPerformance 候选类别。",
            "ufo": "区分持久对象、角色、事件/行动与信息对象，给确定性后处理留下可判定的 kind；不要虚构本体承诺。",
        }[standard["id"]]
        try:
            local_context = standard_assets.prompt_context(standard["id"])
        except Exception as exc:
            local_context = f"本地标准资产未就绪（{str(exc)[:160]}）；不得声称已经按该标准完成对齐。"
        blocks.append(
            f"[本体标准{'强约束' if standard['mode'] == 'constraint' else '参照'}:{standard['name']} / {standard['version']}]\n"
            f"{detail}\n{local_context}\n{spec['disclaimer']}"
        )
    return "\n\n[构建参照配置]\n" + ("\n\n".join(blocks) if blocks else
           "本轮显式不选择行业参照或本体标准；仅依据输入证据建立本地业务本体。")


def _object_text(obj):
    attrs = " ".join(str(x.get("col") or "") for x in (obj.get("attrs") or []) if isinstance(x, dict))
    return " ".join(str(obj.get(key) or "") for key in ("id", "name", "cn", "table")) + " " + attrs


def _contains(text, term):
    text_low, term_low = text.lower(), term.lower()
    if not term_low:
        return False
    if re.fullmatch(r"[a-z0-9_ ]+", term_low):
        return bool(re.search(r"(?:^|[^a-z0-9])" + re.escape(term_low) + r"(?:$|[^a-z0-9])", text_low))
    return term_low in text_low


def _industry_match(obj, industry_id):
    text = _object_text(obj)
    candidates = []
    for concept in INDUSTRIES[industry_id]["concepts"]:
        hits = [term for term in concept["aliases"] if _contains(text, term)]
        if hits:
            candidates.append((max(len(x) for x in hits), concept, sorted(hits, key=len, reverse=True)))
    if not candidates:
        return None
    _score, concept, hits = max(candidates, key=lambda row: row[0])
    return concept, hits


def _isa95_match(obj):
    text = _object_text(obj).lower()
    matches = []
    for category, terms in ISA95_RULES:
        hits = [term for term in terms if _contains(text, term)]
        if hits:
            matches.append((max(len(term) for term in hits), category, hits))
    if not matches:
        return None
    _score, category, hits = max(matches, key=lambda row: row[0])
    return category, sorted(hits, key=len, reverse=True)


def apply_profile(ir, profile):
    """把选择应用到整份 IR；可重复调用，并会移除上一次选择留下的标准专用字段。"""
    if not isinstance(ir, dict):
        return ir
    profile = normalize(profile)
    industry = profile["industry"]
    standard = profile["ontology_standard"]
    objects = [x for x in (ir.get("objects") or []) if isinstance(x, dict)]
    relations = ir.get("links") if ir.get("links") else (ir.get("relations") or [])

    for obj in objects:
        for key in ("industry_reference", "standard_alignment", "reused_from",
                    "foundational_kind", "bfo"):
            obj.pop(key, None)
        if industry["selected"]:
            match = _industry_match(obj, industry["id"])
            if match:
                concept, hits = match
                obj["industry_reference"] = {
                    "industry": industry["id"], "industry_name": industry["name"],
                    "version": industry["version"], "concept": concept["id"],
                    "concept_name": concept["name"], "status": "candidate",
                    "matched_by": hits[:5],
                }
        if standard["id"] == "bfo_iof":
            category = ontology_grounding.default_category(obj.get("kind"))
            obj["bfo"] = category
            obj["standard_alignment"] = {
                "standard": "bfo_iof", "candidate": category,
                "status": "validated_by_kind", "version": standard["version"],
            }
        elif standard["id"] == "isa95":
            match = _isa95_match(obj)
            if match:
                category, hits = match
                obj["standard_alignment"] = {
                    "standard": "isa95", "candidate": category,
                    "status": "candidate", "version": standard["version"],
                    "matched_by": hits[:5],
                }
                obj["reused_from"] = f"ISA-95::{category}"
        elif standard["id"] == "ufo":
            candidate = UFO_KINDS.get(obj.get("kind"))
            if candidate:
                obj["foundational_kind"] = candidate
                obj["standard_alignment"] = {
                    "standard": "ufo", "candidate": candidate,
                    "status": "candidate", "version": standard["version"],
                }

    categories = {(obj.get("id") or obj.get("name")): obj.get("bfo") for obj in objects}
    for relation in relations:
        if not isinstance(relation, dict):
            continue
        if standard["id"] != "bfo_iof":
            for key in ("founded_relation", "grounding_iri", "grounding_status",
                        "grounding_reason", "temporal"):
                relation.pop(key, None)
            continue
        source = relation.get("source") or relation.get("source_concept")
        target = relation.get("target") or relation.get("target_concept")
        item = ontology_grounding.normalize(
            relation.get("founded_relation"), relation.get("temporal"), relation.get("verb"),
            categories.get(source), categories.get(target),
        )
        relation.update({"founded_relation": item["relation"], "grounding_iri": item["iri"],
                         "grounding_status": item["status"], "grounding_reason": item["reason"],
                         "temporal": item["temporal"]})

    ir.setdefault("scenario", {})["build_references"] = copy.deepcopy(profile)
    return ir


def evaluate(ir, profile, grounding=None):
    """评估行业/标准选择；返回可并入 build_quality 的阻断项、待审项与待补项。"""
    profile = normalize(profile)
    objects = [x for x in (ir.get("objects") or []) if isinstance(x, dict)] if isinstance(ir, dict) else []
    blocking, review, gaps = [], [], []

    industry_cfg = profile["industry"]
    industry_report = {"selected": industry_cfg["selected"], "id": industry_cfg["id"],
                       "name": industry_cfg["name"], "mode": industry_cfg["mode"],
                       "matched_concepts": [], "missing_required": [], "missing_optional": [], "coverage": None}
    if industry_cfg["selected"]:
        spec = INDUSTRIES[industry_cfg["id"]]
        matched = sorted({(obj.get("industry_reference") or {}).get("concept") for obj in objects
                          if (obj.get("industry_reference") or {}).get("industry") == industry_cfg["id"]} - {None})
        required = [x["id"] for x in spec["concepts"] if x.get("required")]
        name_by_id = {x["id"]: x["name"] for x in spec["concepts"]}
        missing = [x for x in required if x not in matched]
        industry_report.update(
            matched_concepts=[name_by_id[x] for x in matched if x in name_by_id],
            missing_required=[name_by_id[x] for x in missing],
            # 可选概念不参与判定,但要列出——例如能碳参照下数据只覆盖能源侧时,
            # 排放源/排放因子等碳侧概念全部缺席,这正是使用者最需要看到的覆盖缺口。
            missing_optional=[name_by_id[x] for x in name_by_id if x not in matched and x not in required],
            coverage=round(len(set(required) & set(matched)) / len(required), 4) if required else 1.0,
        )
        if missing:
            item = {"type": "industry_reference_gaps", "industry": industry_cfg["id"],
                    "count": len(missing),
                    "desc": f"{industry_cfg['name']}核心参照项缺失:{'、'.join(industry_report['missing_required'])}",
                    "fix": "补充相应数据/文档依据后重新构建；没有依据时登记为已知未满足项，不得编造对象。"}
            gaps.append(item)
            (blocking if industry_cfg["mode"] == "constraint" else review).append(item)

    standard_cfg = profile["ontology_standard"]
    aligned = [obj for obj in objects if (obj.get("standard_alignment") or {}).get("standard") == standard_cfg["id"]]
    standard_report = {"selected": standard_cfg["selected"], "id": standard_cfg["id"],
                       "name": standard_cfg["name"], "mode": standard_cfg["mode"],
                       "objects_aligned": len(aligned), "objects_total": len(objects),
                       "relation_mapped": int((grounding or {}).get("mapped") or 0),
                       "asset": asset_trace(profile), "issues": []}
    standard_issues = []
    if standard_cfg["selected"] and not (standard_report.get("asset") or {}).get("ready"):
        standard_issues.append({"type": "standard_asset_unavailable", "count": 1,
                                "desc": f"{standard_cfg['name']} 本地机器可读资产未就绪",
                                "fix": "恢复 ontology/standards 中清单声明的文件并通过解析检查后重新构建。"})
    if standard_cfg["id"] == "bfo_iof":
        missing = [obj.get("cn") or obj.get("name") or "?" for obj in objects if not obj.get("bfo")]
        invalid = int((grounding or {}).get("issue_count") or 0)
        if missing:
            standard_issues.append({"type": "standard_objects_unclassified", "count": len(missing),
                                    "desc": f"{len(missing)} 个对象未获得 BFO/IOF 上层类别",
                                    "fix": "复核对象 kind 后重新执行标准对齐。"})
        if invalid:
            standard_issues.append({"type": "standard_invalid_relation_mapping", "count": invalid,
                                    "desc": f"{invalid} 条声明的 BFO/IOF 关系不满足官方名称或类型约束",
                                    "fix": "修正类别/方向，无法确认时改为本地关系。"})
    elif standard_cfg["id"] == "isa95" and objects and not aligned:
        standard_issues.append({"type": "standard_no_alignment", "count": len(objects),
                                "desc": f"当前对象没有可复核的 {standard_cfg['name']} 候选对齐",
                                "fix": "复核对象命名/kind 与建模范围；不要为了通过检查虚构标准类别。"})
    elif standard_cfg["id"] == "ufo" and len(aligned) < len(objects):
        missing = len(objects) - len(aligned)
        standard_issues.append({"type": "standard_objects_unclassified", "count": missing,
                                "desc": f"{missing} 个对象未获得 {standard_cfg['name']} 基础类型候选",
                                "fix": "复核对象 kind 后重新执行候选对齐；未知类型不得冒充标准类型。"})
    standard_report["issues"] = standard_issues
    for item in standard_issues:
        gaps.append(item)
        (blocking if standard_cfg["mode"] == "constraint" else review).append(item)

    return {"profile": profile, "industry": industry_report,
            "ontology_standard": standard_report,
            "blocking_issues": blocking, "review_queue": review, "gaps": gaps}
