"""Read-only registration of effective legacy IR via injected loaders — DR-059 / DR-065."""
import json

from .payloads import json_text, validate_payload


def register_legacy(store, *, enumerate_graphs, load_effective, provenance):
    """Capture edits and provenance before the atomic catalog transaction; never import server."""
    entries = []
    seen = set()
    for row in enumerate_graphs():
        key, name = row.get("key"), row.get("name")
        if not isinstance(key, str) or not key.strip() or key in seen:
            raise ValueError("旧目录 key 必须非空且唯一。")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("旧目录名称必须非空。")
        seen.add(key)
        before = json_text(provenance(key))
        content = json_text(load_effective(key)).encode("utf-8")
        validate_payload("data", "datamind_ir", content)
        if before != json_text(provenance(key)):
            raise ValueError("读取期间来源或编辑已变化，请重新登记快照。")
        # Detach from callback-owned dictionaries before they can change.
        metadata = {"legacy_key": key, "provenance": json.loads(before)}
        entries.append((key, name, content, metadata))
    return store._register_legacy(entries)
