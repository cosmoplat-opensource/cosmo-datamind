"""Read-only source computation for a separately committed build run — DR-060 / DR-064 / DR-065."""
from copy import deepcopy
from pathlib import Path
import tempfile

from domains.robotics import importer, publisher
from domains.robotics.source_validation import SourceValidationError, issue
from ontology_assets import AssetStore
from srv_context import _RDF_LOCK

from .store import RunStore


def _error(issues):
    return {"result": {"status": "error", "execution_readiness": "not_verified",
                       "task_readiness": "not_assessed", "issues": issues}, "ontology": None}


def compute_build(root, run_id):
    """Compute only. The host owns events, timeout decisions and durable completion.

    Temporary inputs are copies of registered bytes; neither this function nor the
    importer receives a mutable original upload path. No HTTP/server/LLM is imported.
    """
    runs, assets = RunStore(root, read_only=True), AssetStore(root, read_only=True)
    run = runs.get_run(run_id)
    if run["operation"] != "build_ontology" or run["build_input"] is None or run["status"] != "running":
        raise ValueError("只有已开始的来源构建运行可以计算。")
    fixed = run["build_input"]
    contents = {}
    source_ids = {s["role"]: s["source_asset_id"] for s in fixed["sources"]}
    for source in fixed["sources"]:
        try:
            row = assets.get_source(source["source_asset_id"])
            if any(row[key] != source[key] for key in ("source_asset_id", "role", "sha256", "byte_size")):
                raise ValueError("来源登记变化。")
            data = assets.read_source(source["source_asset_id"])
            if len(data) != source["byte_size"]:
                raise ValueError("来源长度变化。")
            contents[source["role"]] = data
        except (OSError, ValueError, KeyError):
            problem = issue("source_integrity", "来源文件缺失、损坏或与快照不符。", source["role"])
            problem["source_asset_id"] = source["source_asset_id"]
            return _error([problem])
    try:
        with tempfile.TemporaryDirectory(prefix="robotics-build-") as raw:
            directory = Path(raw)
            source, models, target = (directory / name for name in ("behavior_tree.xml", "node_models.xml", "candidate.owl"))
            source.write_bytes(contents["behavior_tree"])
            models.write_bytes(contents["node_models"])
            with _RDF_LOCK:
                graph = importer.build_graph(source, models, profile=fixed["profile_id"])
                validation = publisher.publish_ontology(graph, target, mode="real")
            content = target.read_bytes()
        return {"result": {"status": "built", "execution_readiness": "not_verified",
                           "task_readiness": "not_assessed", "issues": [], "validation": validation},
                "ontology": content}
    except SourceValidationError as exc:
        problems = deepcopy(exc.issues)
        for problem in problems:
            problem["source_asset_id"] = source_ids.get(problem["source_role"])
        return _error(problems)
    except Exception:
        # Exception text may contain private server paths or library internals.
        return _error([issue("build_error", "构建或兼容校验失败，未产生可提交的本体候选。", None)])
