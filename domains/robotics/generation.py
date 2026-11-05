"""Strict structured-task generation and explicit authoring drafts — DR-061 / DR-062.

No LLM, source XML, online XML editor or physical robot execution is involved.
"""
from copy import deepcopy
import os
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET

from rdflib import Graph
from rdflib.compare import to_isomorphic
from rdflib.exceptions import Error as RDFError

from .compiler import instantiate
from .consumer import check_mode, validate_graph
from .grounding import resolve_request
from .semantics import xml_from_graph, node_models_from_graph, configuration_paths
from .validator import simulate

TASK_FIELDS = {"operation", "part", "station", "carrier", "slot", "unsupported_requests"}
CLARIFICATION_CODES = {"missing_entity", "unknown_entity", "ambiguous_entity"}


def _result(mode):
    return {"status": "error", "mode": mode, "candidate_written": False, "candidate_path": None,
            "execution_readiness": "not_verified", "issues": [], "parameter_sources": []}


def _issue(result, code, message, *, status="error"):
    result.update(status=status, issues=[{"code": code, "message": str(message)}])
    return result


def _validate_task(task):
    if not isinstance(task, dict) or set(task) != TASK_FIELDS:
        raise ValueError("任务必须且只能包含 operation、part、station、carrier、slot、unsupported_requests 六字段。")
    for field in ("operation", "part", "station", "carrier", "slot"):
        item = task[field]
        if item is not None and (not isinstance(item, str) or not item.strip() or len(item) > 200):
            raise ValueError(f"{field} 必须是非空名称字符串，缺失选择使用 null。")
    restrictions = task["unsupported_requests"]
    if not isinstance(restrictions, list) or len(restrictions) > 20 or any(
        not isinstance(item, str) or not item.strip() or len(item) > 1000 for item in restrictions
    ):
        raise ValueError("unsupported_requests 必须是最多 20 项的非空字符串列表。")


def _snapshot(context):
    if not isinstance(context, dict) or not isinstance(context.get("graph"), Graph):
        raise ValueError("需要 consumer.load_ontology 返回的本体上下文。")
    return deepcopy(context["graph"])


def _check_snapshot(context, graph):
    expected = context.get("graph_digest")
    if expected is None or expected != to_isomorphic(graph).graph_digest():
        raise ValueError("加载后的本体已被修改或未固定版本，请重新发布并加载，不能沿用旧工件摘要生成。")


def _write_xml(root, directory, name):
    """Expose complete bytes only, without replacing another run's artifact."""
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    output = deepcopy(root)
    ET.indent(output, space="  ")
    content = ET.tostring(output, encoding="utf-8", xml_declaration=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".robotics-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return str(target)


def generate_task(context, task, *, mode="real", output_dir):
    """Generate only after explicit task selection, knowledge and template checks."""
    result = _result(mode)
    try:
        _validate_task(task)
    except ValueError as exc:
        return _issue(result, "invalid_task", exc)
    try:
        graph = _snapshot(context)
        try:
            check_mode(graph, mode)
        except ValueError as exc:
            return _issue(result, "mode_mismatch", exc)
        if task["unsupported_requests"]:
            result.update(status="unsupported", issues=[
                {"code": "unsupported_request", "message": item} for item in task["unsupported_requests"]])
            return result
        if task["operation"] is None:
            return _issue(result, "missing_operation", "请明确要生成的任务类型。", status="needs_clarification")
        if task["operation"] != "pick_place":
            return _issue(result, "unsupported_operation", f"当前不支持任务：{task['operation']}", status="unsupported")
        grounding = resolve_request(graph, {field: task[field] for field in ("part", "station", "carrier", "slot")},
                                    mode=mode, revocations=context.get("evidence_revocations", ()))
        result["grounding"] = grounding
        if grounding["issues"]:
            clarification = any(item["code"] in CLARIFICATION_CODES for item in grounding["issues"])
            result.update(status="needs_clarification" if clarification else "needs_information",
                          issues=grounding["issues"])
            return result
        if grounding["config"] is None:
            return _issue(result, "missing_configuration", "本体未提供完整配置。", status="needs_information")
        _check_snapshot(context, graph)
        validation, _ = validate_graph(graph, mode=mode)
        template = xml_from_graph(graph)
        models, paths = node_models_from_graph(graph), configuration_paths(graph)
        candidate = instantiate(template, grounding["config"], baseline=template, models_root=models, config_paths=paths)
        symbolic = simulate(candidate, models, baseline=template, config_paths=paths)
        if not symbolic["valid"]:
            raise ValueError(symbolic["errors"])
        if mode == "demo":
            candidate.insert(0, ET.Comment(" DEMO ONLY - simulated knowledge; NOT FOR ROBOT EXECUTION "))
        validation.update(ontology_sha256=context["validation"]["ontology_sha256"], symbolic_validation=symbolic)
        path = _write_xml(candidate, output_dir, "demo_candidate.xml" if mode == "demo" else "candidate.xml")
        result.update(status="generated", candidate_written=True, candidate_path=path,
                      parameter_sources=grounding["parameter_sources"], validation=validation)
    except OSError as exc:
        return _issue(result, "artifact_write", f"候选未保存：{exc}")
    except (ValueError, TypeError, KeyError, IndexError, RDFError) as exc:
        return _issue(result, "validation_error", str(exc)[:1600])
    return result


def create_authoring_draft(context, config, *, output_dir):
    """Save a clearly marked debugging artifact; never return a strict candidate."""
    result = _result("authoring")
    result["draft_path"] = None
    try:
        graph = _snapshot(context)
        _check_snapshot(context, graph)
        template = xml_from_graph(graph)
        models, paths = node_models_from_graph(graph), configuration_paths(graph)
        if not isinstance(config, dict) or set(config) - set(paths):
            raise ValueError("草稿配置包含未知技能或不是对象。")
        complete, issues = {}, []
        for skill, fields in paths.items():
            supplied = config.get(skill, {})
            if not isinstance(supplied, dict) or set(supplied) - set(fields):
                raise ValueError(f"{skill} 草稿包含非公开参数。")
            complete[skill] = {}
            for field in fields:
                raw = supplied.get(field)
                if raw is None or (isinstance(raw, str) and not raw.strip()):
                    raw = f"__UNRESOLVED_{skill}_{field}"
                complete[skill][field] = raw
                if isinstance(raw, str) and raw.startswith("__UNRESOLVED_"):
                    issues.append({"code": "unresolved_parameter", "field": f"{skill}.{field}",
                                   "message": f"{skill}.{field} 尚未解决，不能作为正式候选。"})
        candidate = instantiate(template, complete, baseline=template, models_root=models,
                                config_paths=paths, allow_unresolved=True)
        candidate.insert(0, ET.Comment(" AUTHORING DRAFT ONLY - NOT A STRICT CANDIDATE; NOT FOR ROBOT EXECUTION "))
        result.update(status="draft", draft_path=_write_xml(candidate, output_dir, "draft.xml"), issues=issues)
    except OSError as exc:
        return _issue(result, "artifact_write", f"草稿未保存：{exc}")
    except (ValueError, TypeError, KeyError, IndexError, RDFError) as exc:
        return _issue(result, "validation_error", str(exc)[:1600])
    return result
