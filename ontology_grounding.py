#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BFO 2020 / IOF Core 关系映射与类型相容性检查。

关系只有在官方术语存在且源、目标的上层类别满足该关系的定义域和值域时，
才标记为 ``mapped``。无法确认的业务动词保留为本地对象属性，不构造看似属于
BFO/IOF、实际并不存在的关系 IRI。
"""
from __future__ import annotations

from dataclasses import dataclass


KIND_DEFAULTS = {
    "object": "MaterialEntity",
    "event": "Process",
    "action": "PlannedProcess",
    "asset": "MaterialArtifact",
    "role": "Role",
    "ice": "InformationContentEntity",
}

UPPER_CLASS_IRIS = {
    "MaterialEntity": "obo:BFO_0000040",
    "Object": "obo:BFO_0000030",
    "Process": "obo:BFO_0000015",
    "Continuant": "obo:BFO_0000002",
    "Role": "obo:BFO_0000023",
    "Disposition": "obo:BFO_0000016",
    "Function": "obo:BFO_0000034",
    "Quality": "obo:BFO_0000019",
    "InformationContentEntity": "iof:InformationContentEntity",
    "MaterialArtifact": "iof:MaterialArtifact",
    "PlannedProcess": "iof:PlannedProcess",
}

CONTINUANTS = frozenset({
    "Continuant", "MaterialEntity", "Object", "MaterialArtifact",
    "InformationContentEntity", "Role", "Disposition", "Function", "Quality",
})
INDEPENDENT_CONTINUANTS = frozenset({"MaterialEntity", "Object", "MaterialArtifact"})
SPECIFICALLY_DEPENDENT_CONTINUANTS = frozenset({"Role", "Disposition", "Function", "Quality"})


@dataclass(frozen=True)
class RelationSpec:
    iri: str
    temporal: str
    source_categories: frozenset[str]
    target_categories: frozenset[str]


# BFO IRI 取自 BFO 2020 temporalized-relations profile；IOF IRI 取自 IOF Core。
RELATION_SPECS = {
    "continuantPartOfAtAllTimes": RelationSpec(
        "obo:BFO_0000177", "atAllTimes", CONTINUANTS, CONTINUANTS,
    ),
    "hasContinuantPartAtAllTimes": RelationSpec(
        "obo:BFO_0000110", "atAllTimes", CONTINUANTS, CONTINUANTS,
    ),
    "hasSpecifiedOutput": RelationSpec(
        "iof:hasSpecifiedOutput", "atSomeTime", frozenset({"PlannedProcess"}), CONTINUANTS,
    ),
    "hasOutput": RelationSpec(
        "iof:hasOutput", "atSomeTime", frozenset({"Process", "PlannedProcess"}), CONTINUANTS,
    ),
    "hasInput": RelationSpec(
        "iof:hasInput", "atSomeTime", frozenset({"Process", "PlannedProcess"}), CONTINUANTS,
    ),
    "hasParticipantAtSomeTime": RelationSpec(
        "obo:BFO_0000057", "atSomeTime", frozenset({"Process", "PlannedProcess"}), CONTINUANTS,
    ),
    "participatesInAtSomeTime": RelationSpec(
        "obo:BFO_0000056", "atSomeTime", CONTINUANTS, frozenset({"Process", "PlannedProcess"}),
    ),
    "bearerOf": RelationSpec(
        "obo:BFO_0000196", "notTemporalized", INDEPENDENT_CONTINUANTS,
        SPECIFICALLY_DEPENDENT_CONTINUANTS,
    ),
    "realizes": RelationSpec(
        "obo:BFO_0000055", "notTemporalized", frozenset({"Process", "PlannedProcess"}),
        frozenset({"Role", "Disposition", "Function"}),
    ),
    "describes": RelationSpec(
        "iof:describes", "notTemporalized", frozenset({"InformationContentEntity"}),
        frozenset(UPPER_CLASS_IRIS),
    ),
}

# 只收录方向明确的受控动词。“归属”“服务”“触发”语义依赖上下文，不自动接地。
VERB_RELATIONS = {
    "组成": "continuantPartOfAtAllTimes",
    "包含": "hasContinuantPartAtAllTimes",
    # “产生/输出”本身不能证明输出由目标规范规定，因此映射到更一般的 has output。
    "产生": "hasOutput",
    "输出": "hasOutput",
    "输入": "hasInput",
    "参与": "participatesInAtSomeTime",
    "承载": "bearerOf",
    "实现": "realizes",
    "描述": "describes",
}

ALIASES = {name: name for name in RELATION_SPECS}
ALIASES.update({spec.iri: name for name, spec in RELATION_SPECS.items()})
# 旧 IR 曾写入这个不存在的名称；只能纠正为 BFO 的 bearer of，再做类型检查。
ALIASES["bearerOfAtSomeTime"] = "bearerOf"


def default_category(kind: str | None) -> str:
    return KIND_DEFAULTS.get((kind or "object").strip(), "Continuant")


def normalize(
    founded_relation: str | None,
    temporal: str | None,
    verb: str | None,
    source_category: str | None,
    target_category: str | None,
) -> dict:
    """返回规范关系、官方 IRI、时间说明以及映射状态。"""
    raw = (founded_relation or "").strip()
    canonical = ALIASES.get(raw)
    if canonical is None:
        canonical = VERB_RELATIONS.get((verb or "").strip())
    if canonical is None:
        reason = ("旧值 relatedToAtSomeTime 不是 BFO/IOF 官方关系，已保留为本地关系"
                  if raw == "relatedToAtSomeTime"
                  else "业务动词没有可安全确定的 BFO/IOF 对应关系")
        return {"relation": "", "iri": "", "temporal": "", "status": "unmapped", "reason": reason}

    spec = RELATION_SPECS[canonical]
    source = source_category or "Continuant"
    target = target_category or "Continuant"
    if source not in spec.source_categories or target not in spec.target_categories:
        return {
            "relation": "",
            "iri": "",
            "temporal": "",
            "status": "unmapped",
            "reason": (
                f"{canonical} 的类别约束不满足：源={source}，目标={target}；"
                "关系保留为本地对象属性"
            ),
        }
    return {
        "relation": canonical,
        "iri": spec.iri,
        "temporal": temporal or spec.temporal,
        "status": "mapped",
        "reason": "",
    }


def from_verb(verb: str | None, source_category: str | None = None,
              target_category: str | None = None) -> dict:
    return normalize(None, None, verb, source_category, target_category)
