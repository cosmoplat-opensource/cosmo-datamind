"""Fresh builds, durable runs and actual child-process demonstrations — DR-060 / DR-064 / DR-065."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from rdflib import Graph, Literal, XSD

from domains.robotics import importer, publisher, demo
from domains.robotics.graph import S
from domains.robotics.offline_demo import _verify_candidate
from ontology_assets import AssetStore
from ontology_assets.payloads import json_text
from srv_context import _RDF_LOCK

from .robotics import execution_components, generate_from_version
from .store import RunStore

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/demo_domain_storage.py"
EXPECTED = {"demo-success": "generated", "real-missing-evidence": "needs_information",
            "interrupted-recovery": "interrupted"}


def _write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _child(*args, expected_code=0):
    process = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], cwd=ROOT,
                             capture_output=True, text=True, timeout=60)
    if process.returncode != expected_code:
        raise RuntimeError(f"独立进程退出 {process.returncode}：{process.stderr[-1600:]}")
    return json.loads(process.stdout)


def process_worker(action, root, run_id):
    """Private demo workers, used only with the demo's newly owned storage directory."""
    runs = RunStore(root)
    if action == "crash":
        runs.start(run_id)
        print(json_text({"pid": os.getpid(), "run_id": run_id}), flush=True)
        os._exit(23)
    if runs.get_run(run_id)["status"] != "running":
        raise ValueError("恢复演示没有找到预期的遗留 running。")
    recovered = runs.recover_interrupted()
    if recovered != [run_id] or runs.recover_interrupted():
        raise ValueError("启动恢复未精确、幂等地处理中断运行。")
    print(json_text({"pid": os.getpid(), "recovered": recovered}), flush=True)


def _data_asset(assets):
    content = json_text({"scenario": {"name": "存储演示数据"}, "objects": [
        {"id": "part", "name": "parts", "cn": "零件", "table": "parts"}], "relations": []}).encode("utf-8")
    source = assets.put_source(content, role="data_snapshot", media_type="application/json")
    asset = assets.create_asset(name="演示数据本体", domain="data")
    assets.add_version(asset["ontology_id"], content, payload_kind="datamind_ir",
                       source_asset_ids=[source["source_asset_id"]], metadata={"origin": "synthetic-data-demo"})


def _build_version(assets, directory, case, *, source, models, supplement):
    """Build from owned copies so source registration and compilation use identical bytes."""
    mode = "demo" if case == "demo-success" else "real"
    snapshots = []
    for path, name, role, media in ((source, "source.xml", "behavior_tree", "application/xml"),
                                   (models, "models.xml", "node_models", "application/xml")):
        content = Path(path).read_bytes()
        local = directory / name
        local.write_bytes(content)
        snapshots.append(assets.put_source(content, role=role, media_type=media))
    supplement_bytes = Path(supplement).read_bytes()
    supplement_path = directory / "supplement.json"
    supplement_path.write_bytes(supplement_bytes)
    task = json.loads(supplement_bytes)["task"]
    metadata = {"profile_id": "ur_pick_place", "build_recipe": "bt_xml"}
    with _RDF_LOCK:
        graph = importer.build_graph(directory / "source.xml", directory / "models.xml")
        if mode == "demo":
            applied = demo.apply_demo_supplement(graph, supplement_path)
            graph, task = applied["graph"], applied["task"]
            snapshots.append(assets.put_source(supplement_bytes, role="demo_supplement", media_type="application/json"))
            metadata.update(supplement_sha256=applied["supplement_sha256"], supplement_changes=applied["changes"])
        published = directory / "published.owl"
        publisher.publish_ontology(graph, published, mode=mode)
    asset = assets.create_asset(name=f"机器人本体 · {case}", domain="robotics")
    version = assets.add_version(asset["ontology_id"], published.read_bytes(), payload_kind="robotics_rdf",
                                source_asset_ids=[s["source_asset_id"] for s in snapshots], metadata=metadata)
    version = assets.publish_version(asset["ontology_id"], version["version_id"], mode=mode)
    return asset, version, task, mode, json.loads(supplement_bytes)


def _run_case(root, case, *, source, models, supplement):
    row = {"case": case, "expected_status": EXPECTED[case], "actual_status": "error", "passed": False,
           "issues": [], "execution_readiness": "not_verified"}
    try:
        assets, runs = AssetStore(root), RunStore(root)
        with tempfile.TemporaryDirectory(prefix=".build-", dir=root.parent) as raw:
            asset, version, task, mode, supplement_data = _build_version(
                assets, Path(raw), case, source=source, models=models, supplement=supplement)
        aid, vid = asset["ontology_id"], version["version_id"]
        row.update(ontology_id=aid, version_id=vid)
        original = assets.read_payload(aid, vid)
        if case == "demo-success":
            with _RDF_LOCK:
                graph = Graph().parse(data=original, format="xml")
                graph.set((S.Perception_Current, S.threshold, Literal("0.61", datatype=XSD.string)))
                newer_bytes = graph.serialize(format="xml", encoding="utf-8")
            newer = assets.add_version(aid, newer_bytes, payload_kind="robotics_rdf", parent_version_id=vid,
                                       source_asset_ids=version["source_asset_ids"], metadata=version["metadata"])
            assets.publish_version(aid, newer["version_id"], mode=mode)
            row["newer_version_id"] = newer["version_id"]
        if case == "interrupted-recovery":
            context = assets.load_robotics(aid, vid, mode=mode)
            run = runs.create_run(ontology_id=aid, version_id=vid, operation="generate_bt", task=task, mode=mode,
                session_ref={"namespace": "storage_demo", "id": case},
                components=execution_components(version, context["validation"]))
            row["run_id"] = run["run_id"]
            crashed = _child("--_worker", "crash", "--storage", root, "--run-id", run["run_id"], expected_code=23)
            recovered = _child("--_worker", "recover", "--storage", root, "--run-id", run["run_id"])
            row.update(crash_pid=crashed["pid"], crash_returncode=23, recovery_pid=recovered["pid"],
                       actual_status=runs.get_run(run["run_id"])["status"])
        else:
            run = generate_from_version(root, ontology_id=aid, version_id=vid, task=task, mode=mode,
                                        session_ref={"namespace": "storage_demo", "id": case})
            row.update(run_id=run["run_id"], actual_status=run["result"]["status"], issues=run["result"]["issues"])
            runs.manifest(run["run_id"])
            row["artifacts"] = runs.artifacts(run["run_id"])
            candidates = [item for item in row["artifacts"] if item["kind"] == "candidate"]
            if case == "demo-success" and run["result"]["status"] == "generated":
                if len(candidates) != 1:
                    raise ValueError("演示成功但没有唯一候选。")
                context = assets.load_robotics(aid, vid, mode=mode)
                # Reuse IR-016's full candidate/template check, not just a success status.
                with _RDF_LOCK:
                    row["candidate_sha256"] = _verify_candidate(
                        {**run["result"], "candidate_path": str(root / candidates[0]["path"])}, root,
                        context["graph"], supplement_data["config"])
            elif case == "real-missing-evidence" and (candidates or not run["result"]["issues"]):
                raise ValueError("真实缺证据场景出现候选或缺少问题报告。")
        if assets.read_payload(aid, vid) != original:
            raise ValueError("旧版本在演示期间被改写。")
        row["passed"] = row["actual_status"] == row["expected_status"]
        if not row["passed"] and not row["issues"]:
            row["issues"] = [{"code": "unexpected_status", "message": "实际结果与场景预期不一致。"}]
    except Exception as exc:
        row.update(passed=False, actual_status="error", issues=[
            {"code": "demo_error", "message": f"{type(exc).__name__}: {exc}"[:1600]}])
    return row


def run_demo(case, output_dir, *, source, models, supplement):
    if case not in (*EXPECTED, "all"):
        raise ValueError("未知演示场景。")
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-")
    directory = Path(tempfile.mkdtemp(prefix=stamp, dir=output))
    root = directory / "storage"
    _data_asset(AssetStore(root))
    summary = {"run_dir": str(directory), "storage_root": str(root), "producer_pid": os.getpid(),
               "passed": False, "cases": []}
    for name in list(EXPECTED) if case == "all" else [case]:
        summary["cases"].append(_run_case(root, name, source=source, models=models, supplement=supplement))
    try:
        summary["verification"] = _child("--verify-storage", root)
    except Exception as exc:
        summary["verification"] = {"passed": False, "issues": [str(exc)[:1600]]}
    summary["passed"] = all(row["passed"] for row in summary["cases"]) and summary["verification"]["passed"] is True
    _write_json(directory / "summary.json", summary)
    lines = ["# 本体与运行存储离线演示", "", f"存储目录：{root}", "",
             "| 场景 | 预期 | 实际 | 核验 | run_id |", "|---|---|---|---|---|"]
    for row in summary["cases"]:
        lines.append(f"| {row['case']} | {row['expected_status']} | {row['actual_status']} | "
                     f"{'PASS' if row['passed'] else 'FAIL'} | {row.get('run_id', '未创建')} |")
    lines += ["", f"独立进程核验：{'PASS' if summary['verification']['passed'] else 'FAIL'}", "",
              "任务为固定结构化输入；demo 使用明确标注的模拟资料。未调用模型，机器人执行状态为 not_verified。", ""]
    for row in summary["cases"]:
        lines.append(f"## {row['case']}\n")
        lines += [f"- {item['code']}：{item['message']}" for item in row["issues"]]
        for item in row.get("artifacts", []):
            lines.append(f"- [{item['name']}](storage/{item['path']}) · SHA-256 `{item['sha256']}`")
    (directory / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary
