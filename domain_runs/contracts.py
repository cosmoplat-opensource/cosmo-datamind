"""Serializable run inputs and public artifact contracts — DR-064 / DR-065."""
import json

from ontology_assets.payloads import json_text

RESULT_STATES = {"generated", "needs_information", "needs_clarification", "unsupported", "error"}
COMPONENT_FIELDS = {"profile_id", "compiler_version", "rule_digest", "prompt_version", "model"}
MODEL_FIELDS = {"invoked", "provider", "model", "configuration_id"}


def text(value, field):
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
        raise ValueError(f"{field} 必须是非空且无控制字符的字符串。")
    return value


def object_copy(value, field):
    if not isinstance(value, dict):
        raise ValueError(f"{field} 必须是 JSON 对象。")
    try:
        return json.loads(json_text(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须可序列化为标准 JSON。") from exc


def session(value):
    value = object_copy(value, "session_ref")
    if set(value) != {"namespace", "id"}:
        raise ValueError("会话引用必须且只能包含 namespace 和 id。")
    for field in ("namespace", "id"):
        text(value[field], field)
    return value


def components(value):
    """Allow only non-sensitive scalar model metadata; never echo rejected input."""
    value = object_copy(value, "components")
    if set(value) != COMPONENT_FIELDS:
        raise ValueError("组件快照字段不符合契约。")
    for field in COMPONENT_FIELDS - {"model"}:
        if value[field] is not None:
            text(value[field], field)
    model = value["model"]
    if not isinstance(model, dict) or set(model) - MODEL_FIELDS or type(model.get("invoked")) is not bool:
        raise ValueError("模型快照只接受非敏感配置字段及 invoked 标记。")
    for field in set(model) - {"invoked"}:
        text(model[field], field)
    return value


def completion(result, artifacts):
    result = object_copy(result, "result")
    if not isinstance(result.get("status"), str) or result["status"] not in RESULT_STATES:
        raise ValueError("未知的运行结果状态。")
    if not isinstance(artifacts, list):
        raise ValueError("artifacts 必须是工件列表。")
    checked, names = [], set()
    for item in artifacts:
        if not isinstance(item, dict) or set(item) != {"name", "kind", "media_type", "content"}:
            raise ValueError("工件必须包含名称、类型、媒体类型及字节。")
        name = text(item["name"], "name")
        if name.startswith(".") or any(c in name for c in "/\\:") or name.lower() == "manifest.json":
            raise ValueError("工件名称必须为普通文件名，且不能替换 manifest。")
        if name in names:
            raise ValueError("同次运行的工件名称不能重复。")
        names.add(name)
        kind = text(item["kind"], "kind")
        if kind == "manifest":
            raise ValueError("manifest 必须由运行服务生成。")
        text(item["media_type"], "media_type")
        if not isinstance(item["content"], bytes) or not item["content"]:
            raise ValueError("工件内容必须是非空 bytes。")
        checked.append(dict(item))
    candidates = sum(item["kind"] == "candidate" for item in checked)
    if candidates != (1 if result["status"] == "generated" else 0):
        raise ValueError("候选工件数量与业务结果不一致。")
    if not any(item["kind"] == "validation" for item in checked):
        raise ValueError("完成结果必须附带验证或问题报告。")
    return result, checked
