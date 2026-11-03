"""Replay fresh builds and report actual offline results — DR-060 / DR-065 / ROB-13."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET

from rdflib import Literal, XSD

from . import importer, publisher, consumer, generation, demo, semantics, compiler
from .graph import S, ONTOLOGY, value

EXPECTED = {"demo-success": "generated", "real-missing-evidence": "needs_information", "invalid-contract": "rejected"}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, data):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _signature(node):
    return node.tag, tuple(sorted(node.attrib.items())), tuple(_signature(child) for child in node)


def _verify_candidate(result, directory, graph, config):
    if result.get("candidate_written") is not True or not result.get("candidate_path"):
        raise ValueError("成功状态缺少已写入候选文件。")
    path = Path(result["candidate_path"]).resolve()
    if not path.is_relative_to(directory) or not path.is_file():
        raise ValueError("候选不存在或位于本次场景目录之外。")
    content = path.read_text(encoding="utf-8")
    if "DEMO" not in content or "__UNRESOLVED_" in content:
        raise ValueError("演示候选缺少标记或仍含未解决参数。")
    template = semantics.xml_from_graph(graph)
    expected = compiler.instantiate(template, config, baseline=template,
        models_root=semantics.node_models_from_graph(graph), config_paths=semantics.configuration_paths(graph))
    candidate = ET.fromstring(content)  # Comments are presentation markers, not execution nodes.
    if _signature(candidate) != _signature(expected):
        raise ValueError("候选结构/调用/绑定与本轮本体及补充参数不一致。")
    if result.get("execution_readiness") != "not_verified" or len(result.get("parameter_sources", [])) != 6:
        raise ValueError("候选缺少准确的执行状态或六项参数来源。")
    return digest(path)


def run_case(case, directory, *, source, models, supplement):
    """Only ValueError from the deliberately corrupted artifact counts as rejection."""
    directory = Path(directory).resolve()
    directory.mkdir()
    row = {"case": case, "expected_status": EXPECTED[case], "actual_status": "error",
           "passed": False, "issues": [], "artifacts": []}
    report = {"case": case, "execution_readiness": "not_verified"}
    try:
        inputs = {str(Path(path).resolve()): digest(path) for path in (source, models, supplement)}
        graph = importer.build_graph(source, models)
        report.update(source_sha256=value(graph, S.BehaviorTree_Source, "sha256"),
                      models_sha256=value(graph, S.NodeModels_Source, "sha256"), inputs=inputs)
        if report["source_sha256"] != inputs[str(Path(source).resolve())] or report["models_sha256"] != inputs[str(Path(models).resolve())]:
            raise ValueError("构建期间来源文件发生变化。")
        augmented = None
        if case == "demo-success":
            augmented = demo.apply_demo_supplement(graph, supplement)
            graph = augmented["graph"]
            task = augmented["task"]
            report.update(supplement_sha256=augmented["supplement_sha256"], supplement_changes=augmented["changes"])
        else:
            task = json.loads(Path(supplement).read_text(encoding="utf-8"))["task"]
        mode = "demo" if case == "demo-success" else "real"
        published = directory / "published.owl"
        publisher.publish_ontology(graph, published, mode=mode)
        loaded = consumer.load_ontology(published, mode=mode)
        report.update(loaded["validation"])
        if report["ontology_sha256"] != digest(published):
            raise ValueError("加载结果与本轮实际发布文件摘要不一致。")
        if case == "invalid-contract":
            broken = deepcopy(loaded["graph"])
            broken.set((ONTOLOGY, S.artifactContract, Literal("unknown-v99", datatype=XSD.string)))
            invalid = directory / "invalid-contract.owl"
            broken.serialize(invalid, format="xml")
            report.update(invalid_artifact_sha256=digest(invalid), mutation="artifactContract: unknown-v99")
            try:
                consumer.load_ontology(invalid, mode="real")
            except ValueError as exc:
                row.update(actual_status="rejected", issues=[{"code": "contract_rejected", "message": str(exc)}])
            else:
                row.update(actual_status="accepted", issues=[{"code": "unexpected_acceptance", "message": "不支持的发布协议被加载器接受。"}])
        else:
            result = generation.generate_task(loaded, task, mode=mode, output_dir=directory)
            row.update(actual_status=result["status"], issues=result["issues"])
            report.update(task=task, generation=result)
            if case == "demo-success" and result["status"] == "generated":
                report["candidate_sha256"] = _verify_candidate(result, directory, loaded["graph"], augmented["config"])
            elif case == "real-missing-evidence":
                if result.get("candidate_written") or result.get("candidate_path"):
                    raise ValueError("真实缺证据场景意外返回候选。")
                if result["status"] == "needs_information" and not result["issues"]:
                    raise ValueError("缺证据结果没有说明实际缺口。")
        if case != "demo-success" and list(directory.rglob("*.xml")):
            raise ValueError("负向场景意外生成 XML。")
        if any(digest(path) != original for path, original in inputs.items()):
            raise ValueError("运行期间来源或补充资料发生变化。")
        row["passed"] = row["actual_status"] == row["expected_status"]
    except Exception as exc:
        # An exception is a failed demo, never a successful negative scenario.
        row.update(actual_status="error", passed=False,
                   issues=[{"code": "demo_error", "message": f"{type(exc).__name__}: {exc}"[:2000]}])
    if not row["passed"] and not row["issues"]:
        row["issues"] = [{"code": "unexpected_status", "message": "实际状态与场景预期不一致。"}]
    report.update(actual_status=row["actual_status"], passed=row["passed"], issues=row["issues"])
    write_json(directory / "validation_report.json", report)
    row["artifacts"] = [{"path": str(path.resolve()), "sha256": digest(path)}
                        for path in sorted(directory.iterdir()) if path.is_file()]
    return row


def run_demo(case, output_dir, *, source, models, supplement):
    if case not in (*EXPECTED, "all"):
        raise ValueError("未知演示场景。")
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-")
    directory = Path(tempfile.mkdtemp(prefix=stamp, dir=output_dir))
    cases = list(EXPECTED) if case == "all" else [case]
    summary = {"run_dir": str(directory), "passed": False, "cases": []}
    for name in cases:
        summary["cases"].append(run_case(name, directory / name, source=source, models=models, supplement=supplement))
    summary["passed"] = all(row["passed"] for row in summary["cases"])
    write_json(directory / "summary.json", summary)
    lines = ["# 机器人离线演示结果", "", f"运行目录：{directory}", "",
             "| 场景 | 预期 | 实际 | 检查 |", "|---|---|---|---|"]
    for row in summary["cases"]:
        lines.append(f"| {row['case']} | {row['expected_status']} | {row['actual_status']} | {'PASS' if row['passed'] else 'FAIL'} |")
    lines += ["", "PASS 表示符合该场景预期；不代表机器人已具备现场执行条件。", ""]
    for row in summary["cases"]:
        lines.append(f"## {row['case']}\n")
        lines.extend(f"- {item['code']}：{item['message']}" for item in row["issues"])
        lines.extend(f"- [{Path(item['path']).name}]({Path(item['path']).relative_to(directory).as_posix()}) · SHA-256 `{item['sha256']}`"
                     for item in row["artifacts"])
        lines.append("")
    with (directory / "summary.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(lines))
    return summary
