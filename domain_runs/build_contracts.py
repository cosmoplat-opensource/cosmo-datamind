"""Source snapshots and candidate completion contracts — DR-060 / DR-064 / DR-065."""
from ontology_assets.storage import digest
from ontology_assets.store import _required
from srv_context import _RDF_LOCK

from . import contracts

SOURCE_FIELDS = ("source_asset_id", "role", "sha256", "byte_size")
ROLES = {"behavior_tree", "node_models"}


def source_snapshot(storage, conn, source_asset_ids):
    if (not isinstance(source_asset_ids, list) or len(source_asset_ids) != 2
            or any(not isinstance(sid, str) or not sid for sid in source_asset_ids)
            or len(set(source_asset_ids)) != 2):
        raise ValueError("构建必须指定两个不同的来源 ID。")
    sources = []
    for sid in source_asset_ids:
        source = _required(conn, "SELECT * FROM sources WHERE source_asset_id=?", (sid,))
        if source["role"] not in ROLES or source["media_type"] != "application/xml" or source["byte_size"] <= 0:
            raise ValueError("构建来源必须是带明确角色的非空 XML。")
        content = storage.read_bytes(source["path"], source["sha256"])
        if len(content) != source["byte_size"]:
            raise ValueError("来源长度与登记记录不一致。")
        sources.append({field: source[field] for field in SOURCE_FIELDS})
    if {source["role"] for source in sources} != ROLES:
        raise ValueError("构建必须同时指定行为树和节点模型来源。")
    return sources


def build_input(storage, conn, *, name, recipe, profile_id, source_asset_ids):
    contracts.text(name, "name")
    if recipe != "bt_xml" or profile_id != "ur_pick_place":
        raise ValueError("当前仅支持 bt_xml 配方及 ur_pick_place profile。")
    return {"name": name, "recipe": recipe, "profile_id": profile_id,
            "sources": source_snapshot(storage, conn, source_asset_ids)}


def verify_input(storage, conn, fixed):
    current = build_input(storage, conn, name=fixed["name"], recipe=fixed["recipe"],
                          profile_id=fixed["profile_id"],
                          source_asset_ids=[s["source_asset_id"] for s in fixed["sources"]])
    if current != fixed:
        raise ValueError("来源与本次构建固定输入不一致。")


def completion(result, ontology, fixed):
    """Revalidate actual bytes outside a DB write transaction; never trust a child's flag."""
    result = contracts.object_copy(result, "result")
    if (result.get("status") not in ("built", "error")
            or result.get("execution_readiness") != "not_verified"
            or result.get("task_readiness") != "not_assessed"
            or not isinstance(result.get("issues"), list)):
        raise ValueError("构建完成结果不符合契约。")
    common = {"status", "execution_readiness", "task_readiness", "issues"}
    if result["status"] == "error":
        if set(result) != common or ontology is not None or not result["issues"]:
            raise ValueError("失败构建只能保存问题报告，不能附带本体。")
        for problem in result["issues"]:
            if (not isinstance(problem, dict) or set(problem) !=
                    {"code", "message", "source_role", "source_asset_id", "location"}
                    or not isinstance(problem["location"], dict)):
                raise ValueError("构建问题必须包含来源和位置。")
            contracts.text(problem["code"], "code")
            contracts.text(problem["message"], "message")
        return result
    if set(result) != common | {"validation"} or result["issues"] or not isinstance(ontology, bytes) or not ontology:
        raise ValueError("成功构建必须附带本体字节及兼容报告。")
    reported = result["validation"]
    if not isinstance(reported, dict) or reported.get("ontology_sha256") != digest(ontology):
        raise ValueError("本体字节与构建报告摘要不一致。")
    from domains.robotics.consumer import load_ontology_bytes
    with _RDF_LOCK:
        actual = load_ontology_bytes(ontology, mode="real")["validation"]
    if actual != reported:
        raise ValueError("构建报告与实际消费校验不一致。")
    sources = {s["role"]: s for s in fixed["sources"]}
    if (actual["source_sha256"] != sources["behavior_tree"]["sha256"]
            or actual["models_sha256"] != sources["node_models"]["sha256"]):
        raise ValueError("本体不属于本次固定来源。")
    return result
