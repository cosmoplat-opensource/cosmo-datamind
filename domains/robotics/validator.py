"""Fixed UR structural and symbolic checks, never physical execution — DR-062.

Migrated from yang authoring runtime; draft handling remains an explicit caller policy.
"""
import re
import xml.etree.ElementTree as ET
from .xml_contract import (_trees, _registry, _ports, _reference, _config_locations,
                           _error, _baseline_root, _literal_problem)
from .profiles.ur_pick_place import EXPECTED_STEPS

def _baseline_errors(root, baseline, config_paths):
    """Compare against the ontology template, allowing declared public value slots."""
    allowed = {}
    for section in _config_locations(baseline, config_paths).values():
        for element, attribute in section.values():
            allowed.setdefault(id(element), set()).add(attribute)
    errors = []

    def compare(candidate, original, path):
        if candidate.tag != original.tag or len(candidate) != len(original):
            errors.append(_error("unapproved_structure", path, "XML elements differ from the ontology baseline"))
            return
        permitted = allowed.get(id(original), set())
        for key in set(candidate.attrib) | set(original.attrib):
            if key in permitted and key in candidate.attrib and key in original.attrib:
                value = candidate.get(key)
                if not value.strip() or any(c in value for c in "{}\n\r\0"):
                    errors.append(_error("configuration_literal", path + "@" + key,
                                         "Public configuration must remain a nonempty literal, not a runtime binding"))
                continue
            if candidate.get(key) != original.get(key):
                errors.append(_error("unapproved_change", path + "@" + key,
                                     "Only the six existing public configuration values may change"))
        if (candidate.text or "").strip() != (original.text or "").strip():
            errors.append(_error("unapproved_change", path, "XML text differs from the ontology baseline"))
        for index, (child, old_child) in enumerate(zip(candidate, original), 1):
            compare(child, old_child, path + f"/{old_child.tag}[{index}]")

    compare(root, baseline, "/root")
    return errors


def _structure_errors(root):
    errors = []
    if not isinstance(root, ET.Element):
        return [_error("root_contract", "/root", "Expected an XML root element")]
    trees = _trees(root)
    if root.tag != "root" or root.get("BTCPP_format") != "4" or root.get("main_tree_to_execute") != "main":
        errors.append(_error("root_contract", "/root", "Expected BTCPP_format=4 and entry tree main"))
    if len(root.findall("BehaviorTree")) != 4 or set(trees) != set(EXPECTED_STEPS):
        errors.append(_error("tree_set", "/root", "Expected the four unique UR profile trees"))
    for name, tree in trees.items():
        if len(tree) != 1 or tree[0].tag != "Sequence":
            errors.append(_error("fixed_structure", name, "This interpreter supports Sequence procedures only"))
            continue
        if [node.tag for node in tree[0]] != EXPECTED_STEPS.get(name):
            errors.append(_error("fixed_structure", name, "Steps differ from the supported UR profile"))
        if name == "main" and [node.get("ID") for node in tree[0] if node.tag == "SubTree"] != ["Perception", "grasp", "Place"]:
            errors.append(_error("fixed_structure", name, "Subtree order differs from the supported UR profile"))
        for index, node in enumerate(tree[0], 1):
            if len(node):
                errors.append(_error("leaf_children", f"{name}/{index}", "Skill calls cannot contain child nodes"))
    return errors


def _analyse(root, models_root, baseline=None, config_paths=None):
    errors = _structure_errors(root)
    report = {"mode": "symbolic_offline", "engine": "fixed-sequence Python interpreter, not BehaviorTree.CPP",
              "valid": False, "steps": [], "expected_effects": [], "observed_physical_facts": [],
              "errors": errors, "warnings": [
                  "No ROS, motion, capture, contact, collision, reachability or execution feedback was performed.",
                  "Pose values are symbolic tags; literal target frames are declared assumptions, not measured facts.",
                  "MoveJ retains the ontology six-value flat syntax; compatibility with the declared nested-vector converter is unverified.",
                  "Physical units, calibration, profile compatibility and runtime preconditions require separate confirmation."]}
    if errors:
        return report
    try:
        baseline = _baseline_root(baseline)
        baseline_errors = _baseline_errors(root, baseline, config_paths)
    except ValueError as exc:
        errors.append(_error("missing_ontology_template", "/root", str(exc)))
        return report
    trees = _trees(root)
    registry = _registry(models_root)
    for key, model in _registry(root).items():
        if model.tag == "SubTree" or key not in registry:
            registry[key] = model
        else:
            original = _ports(registry[key])
            embedded = _ports(model)
            if set(original) != set(embedded) or any(
                original[p].tag != embedded[p].tag or original[p].get("type") != embedded[p].get("type")
                for p in set(original) & set(embedded)
            ):
                errors.append(_error("model_conflict", key, "Embedded port names, directions or types conflict with node catalog"))
    for tree_id, tree in trees.items():
        for index, node in enumerate(tree[0], 1):
            path = f"{tree_id}/{index}:{node.tag}"
            if node.tag == "SetBlackboard":
                if set(node.attrib) - {"name", "value", "output_key"} or not {"value", "output_key"} <= set(node.attrib):
                    errors.append(_error("builtin_contract", path, "SetBlackboard requires value and output_key"))
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", node.get("output_key", "")):
                    errors.append(_error("blackboard_key", path, "Invalid output_key"))
                if not _reference(node.get("value", "")):
                    problem = _literal_problem("SetBlackboard", "value", node.get("value", ""), "")
                    if problem:
                        errors.append(_error("unresolved_parameter" if node.get("value", "").startswith("__UNRESOLVED_") else "literal", path, problem))
                continue
            model = registry.get(node.get("ID") if node.tag == "SubTree" else node.tag)
            if model is None:
                errors.append(_error("unknown_node", path, "Node or subtree interface is not registered"))
                continue
            ports = _ports(model)
            metadata = {"name", "ID"} if node.tag == "SubTree" else {"name"}
            for name in set(node.attrib) - set(ports) - metadata:
                errors.append(_error("unknown_port", path, name))
            for name, port in ports.items():
                if port.tag == "input_port" and name not in node.attrib and not port.get("default", "").strip():
                    errors.append(_error("missing_input", path, name))
                if name not in node.attrib:
                    continue
                value = node.get(name)
                if port.tag == "output_port" and not _reference(value):
                    errors.append(_error("output_binding", path, f"{name} must reference a local blackboard key"))
                elif not _reference(value):
                    problem = _literal_problem(node.tag, name, value, port.get("type", ""))
                    if problem:
                        errors.append(_error("unresolved_parameter" if value.startswith("__UNRESOLVED_") else "literal", path, f"{name}: {problem}"))
    if any(item["code"] != "unresolved_parameter" for item in errors):
        errors.extend(baseline_errors)
        return report

    def fail(code, path, message):
        raise ValueError(code, path, message)

    def read(value, board, path):
        key = _reference(value)
        if key is None:
            return value
        if key not in board:
            fail("unassigned_variable", path, f"Local blackboard variable {key!r} has not been produced or remapped")
        return board[key]

    def require(value, kind, path, frame=None, role=None):
        if isinstance(value, str) and value.startswith("__UNRESOLVED_"):
            fail("unresolved_parameter", path, "Semantic value is unresolved in OWL")
        if not isinstance(value, dict) or value.get("kind") != kind:
            fail("semantic_type", path, f"Expected {kind}; received {value}")
        if frame and value.get("frame") != frame:
            if any(str(item).startswith("__UNRESOLVED_") for item in (frame, value.get("frame"))):
                fail("unresolved_parameter", path, "Coordinate frame is unresolved in OWL")
            fail("coordinate_frame", path, f"Expected {frame}; received {value.get('frame')}")
        if role and value.get("role") != role:
            fail("pose_role", path, f"Expected {role}; received {value.get('role')}")

    def execute(tree_id, board):
        for index, node in enumerate(trees[tree_id][0], 1):
            path = f"{tree_id}/{index}:{node.tag}"
            if node.tag == "SubTree":
                child_id = node.get("ID")
                ports = _ports(registry[child_id])
                local = {name: read(node.get(name, port.get("default", "")), board, path + "." + name)
                         for name, port in ports.items() if port.tag == "input_port"}
                execute(child_id, local)
                for name, port in ports.items():
                    if port.tag == "output_port" and name in node.attrib:
                        board[_reference(node.get(name))] = read("{" + name + "}", local, path)
                continue
            if node.tag == "SetBlackboard":
                value = read(node.get("value"), board, path)
                board[node.get("output_key")] = {"kind": "joints", "raw": value, "source": path}
                report["steps"].append({"path": path, "skill": node.tag, "inputs": {"value": value},
                                        "outputs": {node.get("output_key"): board[node.get("output_key")]}})
                continue
            ports = _ports(registry[node.tag])
            inputs = {name: read(node.get(name, port.get("default", "")), board, path + "." + name)
                      for name, port in ports.items() if port.tag == "input_port"}
            for name, value in inputs.items():
                if isinstance(value, str):
                    if value.startswith("__UNRESOLVED_"):
                        continue
                    problem = _literal_problem(node.tag, name, value, ports[name].get("type", ""))
                    if problem:
                        fail("literal", path + "." + name, problem)
                else:
                    symbolic_inputs = {
                        ("MoveJ", "target"): "joints", ("MoveL", "target"): "pose",
                        ("FrameTransform", "pos_source"): "pose", ("FrameTransform", "robot_pose"): "pose",
                        ("ToolPoseCalculate", "object_in_robot"): "pose", ("IncOffsetByObjCor", "origin"): "pose",
                        ("PoseEstimate", "intrinsic"): "intrinsics",
                        ("PoseEstimate", "color_image_topic"): "image_topic",
                        ("PoseEstimate", "depth_image_topic"): "image_topic",
                        ("PoseEstimate", "color_image_frame_id"): "image_frame_id",
                        ("PoseEstimate", "depth_image_frame_id"): "image_frame_id",
                    }
                    kind = symbolic_inputs.get((node.tag, name))
                    if kind is None:
                        fail("semantic_type", path + "." + name, "Expected a configured literal, not runtime output data")
                    require(value, kind, path + "." + name)
            outputs = {}
            if node.tag == "MoveJ":
                target = inputs["target"]
                if isinstance(target, dict):
                    require(target, "joints", path)
                outputs["pose"] = {"kind": "motion_feedback", "source": path}
            elif node.tag == "CaptureFrame":
                channel = inputs["type"]
                for port, kind in (("topic_name", "image_topic"), ("frame_id", "image_frame_id")):
                    outputs[port] = {"kind": kind, "channel": channel, "capture": path,
                                     "service": inputs["service_name"], "exposure": inputs["exposure_time"]}
            elif node.tag == "GetIntrinsic":
                outputs["res"] = {"kind": "intrinsics", "source": path}
            elif node.tag == "PoseEstimate":
                require(inputs["intrinsic"], "intrinsics", path)
                for prefix, channel in (("color", "rgb"), ("depth", "depth")):
                    topic = inputs[prefix + "_image_topic"]
                    frame_id = inputs[prefix + "_image_frame_id"]
                    require(topic, "image_topic", path)
                    require(frame_id, "image_frame_id", path)
                    if any(str(item).startswith("__UNRESOLVED_") for item in (topic["channel"], frame_id["channel"])):
                        fail("unresolved_parameter", path, "Capture channel is unresolved in OWL")
                    if topic["channel"] != channel or frame_id["channel"] != channel or topic["capture"] != frame_id["capture"]:
                        fail("capture_pair", path, "Image topic and image frame ID must come from the same capture and channel")
                outputs["pose"] = {"kind": "pose", "role": "object", "frame": "left_camera", "source": path,
                                   "object_name": inputs["object_name"], "model_name": inputs["model_name"],
                                   "threshold": inputs["threshold"]}
            elif node.tag == "GetTCP":
                outputs["pose"] = {"kind": "pose", "role": "tcp", "frame": "left_robot", "source": path}
            elif node.tag == "FrameTransform":
                require(inputs["pos_source"], "pose", path, frame=inputs["source"], role="object")
                require(inputs["robot_pose"], "pose", path, frame=inputs["target"], role="tcp")
                outputs["pos_target"] = {"kind": "pose", "role": "object", "frame": inputs["target"],
                                         "source": path, "derived_from": inputs["pos_source"],
                                         "calibration": "required_but_not_verified"}
            elif node.tag == "ToolPoseCalculate":
                require(inputs["object_in_robot"], "pose", path, frame="left_robot", role="object")
                outputs["gripper_pos"] = {"kind": "pose", "role": "grasp", "frame": "left_robot", "source": path,
                                          "derived_from": inputs["object_in_robot"],
                                          "robot_ref": inputs["robot_ref"], "gripper_ref": inputs["gripper_ref"]}
            elif node.tag == "IncOffsetByObjCor":
                require(inputs["origin"], "pose", path, frame="left_robot", role="grasp")
                outputs["result"] = {"kind": "pose", "role": "approach", "frame": "left_robot", "source": path,
                                      "derived_from": inputs["origin"], "offset": inputs["offset"]}
            elif node.tag == "MoveL":
                target = inputs["target"]
                if tree_id == "Place" and isinstance(target, str):
                    target = {"kind": "pose", "role": "placement", "frame": "left_robot", "raw": target,
                              "frame_status": "declared_by_profile_not_verified"}
                    inputs["target"] = target
                following = list(trees[tree_id][0])[index:]
                role = "placement" if tree_id == "Place" else (
                    "grasp" if following and following[0].tag == "CloseGripper" else "approach")
                require(target, "pose", path, frame="left_robot", role=role)
                outputs["pose"] = {"kind": "motion_feedback", "source": path}
            elif node.tag in ("InitGripper", "CloseGripper", "OpenGripper"):
                if node.tag != "InitGripper":
                    report["expected_effects"].append({"path": path, "effect": "holding" if node.tag == "CloseGripper" else "released",
                                                       "status": "unverified", "evidence": "No physical observation supplied"})
            for name, port in ports.items():
                if port.tag == "output_port" and name in node.attrib:
                    board[_reference(node.get(name))] = outputs[name]
            report["steps"].append({"path": path, "skill": node.tag, "inputs": inputs, "outputs": outputs})

    try:
        execute(root.get("main_tree_to_execute"), {})
    except ValueError as exc:
        errors.append(_error(*exc.args))
    errors.extend(baseline_errors)
    report["valid"] = not errors
    return report


def validate_tree(root: ET.Element, models_root: ET.Element, baseline=None, config_paths=None) -> list[dict]:
    """Return structural, interface, local-dataflow and symbolic-frame errors."""
    return _analyse(root, models_root, baseline, config_paths)["errors"]


def simulate(root: ET.Element, models_root: ET.Element, baseline=None, config_paths=None) -> dict:
    """Trace sequential symbolic values; never asserts grasp or placement facts."""
    return _analyse(root, models_root, baseline, config_paths)
