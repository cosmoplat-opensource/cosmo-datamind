"""Fixed UR vocabulary, procedure and source-derived examples — DR-060 / DR-062.

Migrated from yang ontology_pipeline; examples remain illustrative, never confirmed.
"""
from ..graph import S, individual, invocation, literal

COMPOSITES = {
    "Perception": ("Perception_Skill", ["position", "exposure_time", "model_name", "threshold", "object_name"], ["pose"]),
    "grasp": ("Grasp_Skill", ["obj_in_robot"], []),
    "Place": ("Place_Skill", ["place_pose"], []),
}


ROLES = {
    "Perception": ["移动至拍照位", "采集RGB", "采集深度", "读取相机内参", "估计物体位姿"],
    "grasp": ["计算抓取位姿", "计算接近位姿", "接近", "到达抓取位", "闭合夹爪", "撤离"],
    "Place": ["移动至中转位", "到达放置位", "打开夹爪", "返回中转位"],
    "main": ["设置初始关节位", "设置拍照关节位", "回初始位", "初始化夹爪", "感知调用", "读取TCP", "相机到机器人坐标转换", "抓取调用", "放置调用", "回初始位"],
}

EXPECTED_STEPS = {
    "Perception": ["MoveJ", "CaptureFrame", "CaptureFrame", "GetIntrinsic", "PoseEstimate"],
    "grasp": ["ToolPoseCalculate", "IncOffsetByObjCor", "MoveL", "MoveL", "CloseGripper", "MoveL"],
    "Place": ["MoveJ", "MoveL", "OpenGripper", "MoveJ"],
    "main": ["SetBlackboard", "SetBlackboard", "MoveJ", "InitGripper", "SubTree", "GetTCP", "FrameTransform", "SubTree", "SubTree", "MoveJ"],
}

def owner(tree_id):
    return S[COMPOSITES[tree_id][0]] if tree_id in COMPOSITES else S.PickPlace_Procedure


def semantic_type(tag, name, cpp_type):
    if name == "target" and tag == "MoveJ":
        return "JointConfiguration"
    if "PoseQuat" in cpp_type:
        return "QuaternionPose"
    if "PoseRPY" in cpp_type:
        return "RPYPose"
    if name == "pose" and tag in ("MoveJ", "MoveL"):
        return "OpaqueMotionFeedback"
    return {"topic_name": "TopicIdentifier", "frame_id": "ImageFrameIdentifier",
            "service_name": "ServiceIdentifier", "action_name": "ActionIdentifier"}.get(name, "RawInterfaceValue")


def add_domain(g, root):
    for name, cls, label, status in [
        ("UR_LeftArm", "RobotArm", "UR左臂", "user_declared"),
        ("LeftGripper", "Gripper", "当前夹爪（/dh接口）", "source_named"),
        ("LeftCamera", "Camera", "左侧相机", "source_named"),
        ("LeftRobotFrame", "CoordinateFrame", "left_robot", "source_named"),
        ("LeftCameraFrame", "CoordinateFrame", "left_camera", "source_named"),
        ("PartType_A", "PartType", "示例零件类型A", "illustrative"),
        ("Part_A_001", "Workpiece", "示例实物零件A", "illustrative"),
        ("Station_B", "Workstation", "示例工位B", "illustrative"),
        ("Carrier_B_01", "Carrier", "示例载具B01", "illustrative"),
        ("Slot_B_01", "PlacementSlot", "示例放置位B01", "illustrative"),
    ]:
        individual(g, S[name], cls, label, status)
    for entity, aliases in {"Part_A_001": ["零部件A", "A"], "Station_B": ["工位B", "B"],
                            "Carrier_B_01": ["载具B01"], "Slot_B_01": ["放置位B01"]}.items():
        for alias in aliases:
            literal(g, S[entity], "alias", alias)
    for subject, prop, obj in [("Part_A_001", "hasPartType", "PartType_A"), ("Station_B", "hasCarrier", "Carrier_B_01"), ("Carrier_B_01", "hasSlot", "Slot_B_01")]:
        g.add((S[subject], S[prop], S[obj]))
    main = root.find("BehaviorTree[@ID='main']/Sequence")
    perception = individual(g, S.Perception_Current, "PerceptionProfile", "源文件当前感知参数；尚未绑定业务零件类型")
    settings = {key: main[4].get(key) for key in ("object_name", "model_name", "threshold", "exposure_time")}
    settings["position"] = main[1].get("value")
    for key, prop in (("object_name", "objectName"), ("model_name", "modelName"), ("threshold", "threshold"), ("exposure_time", "exposureTime")):
        literal(g, perception, prop, settings[key])
    joints = individual(g, S.CaptureJoints_Current, "JointConfiguration", "当前拍照关节配置")
    literal(g, joints, "rawValue", settings["position"])
    literal(g, joints, "unitStatus", "unconfirmed")
    g.add((perception, S.hasCaptureJoints, joints))
    placement = individual(g, S.Placement_Current, "PlacementProfile", "源文件当前放置目标；尚未绑定业务槽位")
    pose = individual(g, S.PlacePose_Current, "CartesianPose", "当前机器人放置目标原始值")
    literal(g, pose, "rawValue", main[8].get("place_pose"))
    literal(g, pose, "unitStatus", "unconfirmed")
    literal(g, pose, "frameStatus", "requires_driver_confirmation")
    literal(g, pose, "orientationRepresentation", "PoseQuat_layout_to_verify")
    g.add((placement, S.hasTargetPose, pose))
    task = individual(g, S.Example_Task_A_to_B, "Task", "示例：将零部件A放到工位B载具", "illustrative")
    g.add((task, S.actsOn, S.Part_A_001))
    g.add((task, S.hasDestination, S.Slot_B_01))
    g.add((task, S.usesProcedure, S.PickPlace_Procedure))
    g.add((task, S.usesResource, S.UR_LeftArm))
    for tree in root.findall("BehaviorTree"):
        for index, node in enumerate(tree[0], 1):
            resource = "LeftCamera" if node.tag in ("CaptureFrame", "GetIntrinsic", "PoseEstimate") else "LeftGripper" if "Gripper" in node.tag else "UR_LeftArm" if node.tag in ("MoveJ", "MoveL", "GetTCP", "FrameTransform", "ToolPoseCalculate") else None
            if resource:
                g.add((invocation(tree.get("ID"), index), S.usesResource, S[resource]))


def add_conditions(g):
    conditions = [
        ("PoseInRobotFrame", "precondition", "输入物体位姿应处于机器人坐标系", "static_symbolic", "coordinate_frame", ["Grasp_Skill", "ToolPoseCalculate_Skill"]),
        ("InputAvailable", "precondition", "输入数据已由前序步骤或父接口提供", "static_dataflow", "unassigned_variable", ["Perception_Skill", "Grasp_Skill", "Place_Skill"]),
        ("GraspCompatible", "applicability", "零件适用于当前固定抓取配置", "evidence_registry_required", "grasp_compatibility", ["Grasp_Skill"]),
        ("PlaceCompatible", "applicability", "目标适用于当前中转与释放配置", "evidence_registry_required", "placement_compatibility", ["Place_Skill"]),
        ("CalibrationKnown", "precondition", "标定与参考键解析已确认", "external_configuration_required", "calibration", ["Grasp_Skill", "FrameTransform_Skill"]),
        ("GripperReady", "precondition", "夹爪处于适合抓取的初始状态", "runtime_feedback_required", "gripper_ready", ["Grasp_Skill"]),
        ("HoldingObserved", "precondition", "已获得零件稳定夹持反馈", "runtime_feedback_required", "holding_feedback", ["Place_Skill"]),
        ("SlotAvailable", "precondition", "目标槽位可用", "runtime_feedback_required", "slot_available", ["Place_Skill"]),
        ("HoldingExpected", "expected_effect", "期望零件被稳定夹持；不是已观测事实", "runtime_feedback_required", "holding_feedback", ["Grasp_Skill", "CloseGripper_Skill"]),
        ("PlacedExpected", "expected_effect", "期望零件完成放置；不是已观测事实", "runtime_feedback_required", "placement_feedback", ["Place_Skill", "OpenGripper_Skill"]),
    ]
    relation = {"precondition": S.hasPrecondition, "applicability": S.hasApplicabilityCondition, "expected_effect": S.hasExpectedEffect}
    for name, kind, label, mechanism, rule, skills in conditions:
        item = individual(g, S[name], "SkillCondition", label, "specified_requirement")
        for prop, val in (("conditionKind", kind), ("checkMechanism", mechanism), ("ruleId", rule)):
            literal(g, item, prop, val)
        for skill in skills:
            g.add((S[skill], relation[kind], item))
