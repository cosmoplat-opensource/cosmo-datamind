"""Validate typed payloads without converting their authoritative bytes — DR-059 / DR-060."""
import json
from xml.sax import SAXException

from rdflib import Graph
from rdflib.exceptions import Error as RDFError

KINDS = {"data": "datamind_ir", "robotics": "robotics_rdf"}


def json_text(value):
    """Canonical JSON for metadata and explicit legacy snapshots, never for supplied OWL."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def data_ir(content):
    try:
        value = json.loads(content)
        # Reject nonstandard NaN/Infinity as well as malformed containers.
        json_text(value)
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ValueError("数据本体必须是有效的 JSON IR。") from exc
    if not isinstance(value, dict) or not isinstance(value.get("objects"), list):
        raise ValueError("数据本体必须包含 objects 列表。")
    for field in ("objects", "links", "relations"):
        if field in value and (not isinstance(value[field], list) or
                               any(not isinstance(item, dict) for item in value[field])):
            raise ValueError(f"IR 的 {field} 必须是对象列表。")
    return value


def validate_payload(domain, kind, content):
    if kind != KINDS.get(domain):
        raise ValueError("载荷类型与本体领域不一致。")
    if not isinstance(content, bytes) or not content:
        raise ValueError("载荷必须为非空 bytes。")
    if domain == "data":
        data_ir(content)
        return
    try:
        text = content.decode("utf-8-sig")
        if "<!DOCTYPE" in text or "<!ENTITY" in text:
            raise ValueError("RDF/XML 不允许 DTD 或实体声明。")
        # Candidate storage checks syntax only; publication uses the consumer contract.
        Graph().parse(data=text, format="xml")
    except (UnicodeError, SAXException, RDFError) as exc:
        raise ValueError("机器人候选必须是 UTF-8 RDF/XML。") from exc
