"""Read-only verification of persisted catalog, history and actual files — DR-064 / DR-065."""
import json
import os

from ontology_assets import AssetStore

from .store import RunStore


def verify_storage(root):
    """Check integrity without migrations, initialization, recovery or re-generation."""
    assets, runs = AssetStore(root, read_only=True), RunStore(root, read_only=True)
    catalog, history = assets.list_assets(), runs.list_runs()
    if not catalog or not history:
        raise ValueError("演示核验需要已有资产和运行，不能把空目录检查视为成功。")
    with runs.storage.transaction() as conn:
        if [row[0] for row in conn.execute("PRAGMA quick_check")] != ["ok"]:
            raise ValueError("SQLite 完整性检查失败。")
        if conn.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("存储包含无效外键引用。")
        for source in conn.execute("SELECT * FROM sources"):
            content = runs.storage.read_bytes(source["path"], source["sha256"])
            if len(content) != source["byte_size"]:
                raise ValueError("来源文件长度与登记记录不一致。")
    for asset in catalog:
        for version in assets.list_versions(asset["ontology_id"]):
            content = assets.read_payload(asset["ontology_id"], version["version_id"])
            if len(content) != version["byte_size"]:
                raise ValueError("本体载荷长度与登记记录不一致。")
            for sid in version["source_asset_ids"]:
                assets.read_source(sid)
    for run in history:
        rid = run["run_id"]
        with runs.storage.transaction() as conn:
            runs._verify_snapshot(conn, run)
        events = runs.events(rid)
        states = [event["payload"]["status"] for event in events if event["kind"] == "state"]
        if [event["seq"] for event in events] != list(range(1, len(events) + 1)):
            raise ValueError("运行事件序号不连续。")
        if not states or states[0] != "queued" or states[-1] != run["status"]:
            raise ValueError("运行与状态事件不一致。")
        artifacts = runs.artifacts(rid)
        if run["status"] in ("completed", "failed"):
            runs.manifest(rid)  # Includes all referenced files and the pinned result.
            candidates = [a for a in artifacts if a["kind"] == "candidate"]
            if len(candidates) != (1 if run["result"]["status"] == "generated" else 0):
                raise ValueError("候选数量与持久化结果不一致。")
            for item in artifacts:
                content = runs.read_artifact(rid, item["artifact_id"])
                if len(content) != item["byte_size"]:
                    raise ValueError("工件长度与登记记录不一致。")
                if run["operation"] == "generate_bt" and item["kind"] == "validation":
                    if json.loads(content) != run["result"]:
                        raise ValueError("生成报告与运行结果不一致。")
        elif artifacts or run["result"] is not None:
            raise ValueError("未完成运行出现已登记产物或结果。")
    return {"passed": True, "pid": os.getpid(), "storage_root": str(assets.storage.root),
            "run_ids": [run["run_id"] for run in history], "assets": catalog}
