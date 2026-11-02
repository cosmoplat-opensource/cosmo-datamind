"""Ground task entities, profiles and applicability evidence — DR-061 / DR-062 / DR-065.

Migrated from the T01 hash-verified yang agent. Reads graph knowledge only;
user selection never modifies confirmation status or creates evidence.
"""
from datetime import datetime, timezone
import re
from rdflib import Literal, RDF, RDFS, XSD
from .graph import S, value, local_name
from .consumer import check_mode
from .semantics import xml_from_graph, node_models_from_graph, configuration_paths
from .compiler import instantiate


def profile_parameters(g, profile, skill):
    """Derive the existing public JSON interface from one structured source."""
    if list(g.objects(profile, S.profileValues)):
        raise ValueError("旧profileValues需迁移为结构化参数；不能同时维护两套配置。")

    def one(subject, prop):
        items = list(g.objects(subject, S[prop]))
        if len(items) != 1:
            raise ValueError(f"{local_name(subject)}.{prop}必须且只能有一个值。")
        return items[0]

    if skill == "Perception":
        fields = [(name, profile, prop, [prop]) for name, prop in (
            ("object_name", "objectName"), ("model_name", "modelName"),
            ("threshold", "threshold"), ("exposure_time", "exposureTime"))]
        fields.append(("position", one(profile, "hasCaptureJoints"), "rawValue", ["hasCaptureJoints", "rawValue"]))
    else:
        fields = [("place_pose", one(profile, "hasTargetPose"), "rawValue", ["hasTargetPose", "rawValue"])]
    values, sources = {}, []
    for name, entity, prop, path in fields:
        raw = one(entity, prop)
        if not isinstance(raw, Literal) or raw.datatype not in (None, XSD.string) or raw.language:
            raise ValueError(f"{local_name(entity)}.{prop}必须是原接口字符串。")
        values[name] = str(raw)
        sources.append({"skill": skill, "parameter": name, "value": str(raw),
                        "profile": local_name(profile), "profile_iri": str(profile),
                        "entity_iri": str(entity), "property": str(S[prop]),
                        "property_path": [str(S[item]) for item in path],
                        "confirmation_status": value(g, profile, "confirmationStatus")})
    return values, sources


def resolve_request(g, request, *, mode="real"):
    """Ground names without inventing entities, configuration, or confirmation."""
    result = {"part": None, "part_type": None, "station": None, "carrier": None,
              "slot": None, "issues": [], "config": None, "profiles": {}, "parameter_sources": []}
    issues = result["issues"]

    def candidates(items):
        return [{"id": local_name(item), "label": str(g.value(item, RDFS.label) or local_name(item))}
                for item in sorted(set(items), key=str)]

    def issue(code, field, message, items=(), **extra):
        issues.append({"code": code, "field": field, "message": message,
                       "candidates": candidates(items), **extra})

    try:
        check_mode(g, mode)
    except ValueError as exc:
        issue("mode_mismatch", "mode", str(exc))
        return result
    allowed = {"instruction", "part", "station", "carrier", "slot"}
    if not isinstance(request, dict) or set(request) - allowed:
        issue("invalid_request", "request", "任务请求只接受instruction/part/station/carrier/slot。")
        return result
    if any(v is not None and (not isinstance(v, str) or not v.strip()) for v in request.values()):
        issue("invalid_request", "request", "任务标识须为非空字符串，缺失字段可省略或使用null。")
        return result

    def normalized(text):
        return "".join(text.split())

    def matches(item, name):
        names = {str(item), local_name(item), *(str(v) for v in g.objects(item, S.alias)),
                 *(str(v) for v in g.objects(item, RDFS.label))}
        return normalized(name) in {normalized(text) for text in names}

    def select(items, field, label, required=True):
        items = sorted(set(items), key=str)
        name = request.get(field)
        found = [item for item in items if matches(item, name)] if name else items
        if required and not name:
            issue("missing_entity", field, f"请指定{label}。", items)
            return None
        if not found:
            issue("unknown_entity", field, f"未找到匹配的{label}，请从已登记实体中选择。", items)
            return None
        if len(found) > 1:
            issue("ambiguous_entity", field, f"存在多个{label}候选，请指定一个。", found)
            return None
        result[field] = local_name(found[0])
        return found[0]

    part = select(g.subjects(RDF.type, S.Workpiece), "part", "零件", True)
    station = select(g.subjects(RDF.type, S.Workstation), "station", "工位", True)
    if part is None or station is None:
        return result
    carriers = list(g.objects(station, S.hasCarrier))
    if request.get("slot") and not request.get("carrier"):
        matching = [carrier for carrier in carriers if any(matches(slot, request["slot"]) for slot in g.objects(carrier, S.hasSlot))]
        if matching:
            carriers = matching
    # A single registered carrier is not a user choice; an explicit slot can identify it.
    carrier = select(carriers, "carrier", "该工位的载具", required=not request.get("slot"))
    if carrier is None:
        return result
    slot = select(g.objects(carrier, S.hasSlot), "slot", "该载具的放置位")
    if slot is None:
        return result
    part_types = list(g.objects(part, S.hasPartType))
    if len(part_types) != 1:
        issue("part_type_mapping", "part", "零件缺少唯一的零件类型映射。", part_types)
        return result
    part_type = part_types[0]
    result["part_type"] = local_name(part_type)
    pp = list(g.objects(part_type, S.hasPerceptionProfile))
    dp = list(g.objects(slot, S.hasPlacementProfile))
    expected_status = "simulated" if mode == "demo" else "confirmed"
    for item, field in ((part, "part"), (part_type, "part"), (station, "station"), (carrier, "carrier"), (slot, "slot")):
        if value(g, item, "confirmationStatus") != expected_status:
            issue("unconfirmed_business_entity", field, f"{local_name(item)}尚无当前模式要求的业务确认。", entity=local_name(item))
    if len(pp) != 1:
        issue("perception_mapping", "part", "缺少唯一的业务零件→感知参数映射，不能把A自动等同于ac07。")
    if len(dp) != 1:
        issue("placement_mapping", "slot", "缺少唯一的槽位→机器人目标位姿映射。")
    for kind, implementation in (("grasp_compatibility", S.grasp_Subtree_Impl), ("placement_compatibility", S.Place_Subtree_Impl)):
        if not compatibility_confirmed(g, kind, implementation, part_type, slot, mode=mode):
            issue(kind, "part" if kind == "grasp_compatibility" else "slot", "缺少对应零件/实现/目标的有效、带来源且匹配当前XML版本的适用性确认。")
    if len(pp) == len(dp) == 1:
        for profile in (pp[0], dp[0]):
            if value(g, profile, "confirmationStatus") != expected_status:
                issue("unconfirmed_profile", "configuration", f"{local_name(profile)}的配置状态尚未确认。", entity=local_name(profile))
        result["profiles"] = {"Perception": local_name(pp[0]), "Place": local_name(dp[0])}
        try:
            perception, perception_sources = profile_parameters(g, pp[0], "Perception")
            placement, placement_sources = profile_parameters(g, dp[0], "Place")
            config = {"Perception": perception, "grasp": {}, "Place": placement}
            template = xml_from_graph(g)
            instantiate(template, config, baseline=template, models_root=node_models_from_graph(g),
                        config_paths=configuration_paths(g))
        except (ValueError, TypeError, KeyError) as exc:
            issue("invalid_profile", "configuration", f"图谱配置不符合现有接口：{exc}")
        else:
            if not issues:
                result["config"] = config
                result["parameter_sources"] = perception_sources + placement_sources
    return result


def compatibility_confirmed(g, kind, implementation, part_type, slot, *, mode="real"):
    try:
        check_mode(g, mode)
    except ValueError:
        return False
    source_hash = value(g, S.BehaviorTree_Source, "sha256")
    if not re.fullmatch(r"[0-9a-f]{64}", source_hash):
        return False
    expected_status = "simulated" if mode == "demo" else "confirmed"
    for item in g.subjects(RDF.type, S.ReadinessEvidence):
        if (item, S.forImplementation, implementation) not in g or (item, S.forPartType, part_type) not in g:
            continue
        if kind == "placement_compatibility" and (item, S.forSlot, slot) not in g:
            continue
        if value(g, item, "evidenceKind") != kind or value(g, item, "confirmationStatus") != expected_status:
            continue
        if value(g, item, "sourceHash") != source_hash:
            continue
        if value(g, item, "evidenceReference").strip() in ("None", ""):
            continue
        try:
            expiry = datetime.fromisoformat(value(g, item, "validUntil").replace("Z", "+00:00"))
            if expiry.tzinfo is not None and expiry > datetime.now(timezone.utc):
                return True
        except ValueError:
            continue
    return False
