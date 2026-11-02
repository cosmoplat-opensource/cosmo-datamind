"""Shared XML interface and literal helpers — DR-060 / DR-062."""
import json
import math
import re
import xml.etree.ElementTree as ET

VAR = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}\Z")


def _trees(root):
    return {tree.get("ID"): tree for tree in root.findall("BehaviorTree")}


def _registry(root):
    return {model.get("ID"): model for model in root.findall("TreeNodesModel/*")}


def _ports(model):
    return {port.get("name"): port for port in model}


def _reference(value):
    match = VAR.fullmatch(value)
    return match.group(1) if match else None


def _config_locations(root, config_paths):
    """Resolve locations derived from ontology bindings, never from step constants."""
    if config_paths is None:
        raise ValueError("缺少本体参数绑定位置，禁止使用源XML或固定步骤索引回退。")
    trees = _trees(root)
    return {skill: {name: (trees[tree_id][0][index], port)
                    for name, (tree_id, index, port) in fields.items()}
            for skill, fields in config_paths.items()}


def _error(code, path, message):
    return {"severity": "error", "code": code, "path": path, "message": message}


def _baseline_root(baseline):
    if not isinstance(baseline, ET.Element):
        raise ValueError("必须显式提供本体构建的内存基线，禁止读取源XML。")
    return baseline


def _literal_problem(tag, port, value, port_type):
    if isinstance(value, str) and value.startswith("__UNRESOLVED_"):
        return "Unresolved ontology parameter: " + value
    if not isinstance(value, str) or not value.strip() or any(c in value for c in "{}\n\r\0"):
        return "Expected a nonempty literal without blackboard syntax or control characters"
    shape = None
    if "PoseQuat" in port_type:
        shape = 7
    elif "PoseRPY" in port_type or (tag == "SetBlackboard" and port == "value"):
        shape = 6
    if shape is not None or "vector" in port_type:
        try:
            numbers = json.loads(value)
            if not isinstance(numbers, list) or not numbers:
                raise ValueError()
            rows = numbers if tag == "MoveJ" and port == "target" and isinstance(numbers[0], list) else [numbers]
            wanted = 6 if tag == "MoveJ" and port == "target" else shape
            for row in rows:
                if not isinstance(row, list) or not row or (wanted and len(row) != wanted):
                    raise ValueError()
                if any(type(n) not in (float, int) or not math.isfinite(n) for n in row):
                    raise ValueError()
                if shape == 7 and math.isclose(sum(n * n for n in row[3:]), 0):
                    raise ValueError()
                if port in ("vel", "acc", "radius") and any(n < 0 for n in row):
                    raise ValueError()
        except (ValueError, TypeError):
            return f"Invalid finite numeric array for {port_type or tag + '.' + port}"
    elif port in ("timeout_s", "exposure_time", "threshold", "acc", "force", "width", "vel"):
        try:
            number = float(value)
            if not math.isfinite(number) or (port != "threshold" and number < 0):
                raise ValueError()
        except ValueError:
            return "Expected a finite numeric literal; nonnegative except unspecified threshold semantics"
    elif port == "type" and tag == "CaptureFrame" and value not in ("rgb", "depth"):
        return "This fixed procedure only supports rgb and depth captures"
    return None
