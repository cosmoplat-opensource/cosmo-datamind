"""RDF vocabulary and construction helpers migrated from yang — DR-059 / DR-060."""
from rdflib import BNode, Graph, Literal, Namespace, RDF, RDFS, OWL, XSD, URIRef
from rdflib.collection import Collection

S = Namespace("https://example.org/ur-skill#")


ONTOLOGY = URIRef("https://example.org/ur-skill")


CLASSES = {
    "Skill": "技能", "AtomicSkill": "原子技能", "CompositeSkill": "复合技能",
    "SkillInvocation": "技能调用", "SkillParameter": "技能参数定义",
    "ParameterBinding": "参数绑定", "DataReference": "流程数据引用",
    "SkillImplementation": "技能实现", "ImplementationConfiguration": "固定实现配置",
    "SkillCondition": "技能条件", "Task": "任务", "TaskProcedure": "任务流程",
    "ExecutionRecord": "执行记录", "Observation": "观测证据", "Resource": "设备资源",
    "RobotArm": "机械臂", "Gripper": "夹爪", "Camera": "相机",
    "PartType": "零件类型", "Workpiece": "实物零件", "Workstation": "工位",
    "Carrier": "载具", "PlacementSlot": "放置位", "CoordinateFrame": "坐标系",
    "CartesianPose": "笛卡尔位姿", "JointConfiguration": "关节配置",
    "CalibrationTransform": "标定变换", "PerceptionProfile": "感知参数配置",
    "PlacementProfile": "放置目标配置", "PortContract": "原始端口契约",
    "SourceDocument": "来源文档", "ReadinessEvidence": "执行准备证据",
}


def invocation(tree_id, index):
    return S[f"{tree_id}_Sequence_{index}"]


def parameter(skill_name, name):
    return S[f"{skill_name}__{name}"]


def reference(tree_id, key):
    return S[f"Data_{tree_id}__{key}"]


def local_name(iri):
    return str(iri).rsplit("#", 1)[-1]


def value(g, subject, prop):
    return str(g.value(subject, S[prop], any=False))


def literal(g, subject, prop, val):
    datatype = XSD.boolean if isinstance(val, bool) else XSD.integer if isinstance(val, int) else XSD.string
    g.add((subject, S[prop], Literal(val, datatype=datatype)))


def individual(g, iri, cls, label, status="source_extracted"):
    g.add((iri, RDF.type, OWL.NamedIndividual))
    g.add((iri, RDF.type, S[cls]))
    g.add((iri, RDFS.label, Literal(label, lang="zh")))
    literal(g, iri, "confirmationStatus", status)
    return iri


def union(g, names):
    if isinstance(names, str):
        return OWL.Thing if names == "Thing" else S[names]
    result, members = BNode(), BNode()
    g.add((result, RDF.type, OWL.Class))
    g.add((result, OWL.unionOf, members))
    Collection(g, members, [S[name] for name in names])
    return result


def schema():
    g = Graph()
    for prefix, ns in (("s", S), ("owl", OWL), ("rdfs", RDFS), ("xsd", XSD)):
        g.bind(prefix, ns)
    g.add((ONTOLOGY, RDF.type, OWL.Ontology))
    g.add((ONTOLOGY, RDFS.label, Literal("UR左臂抓移放技能本体", lang="zh")))
    g.add((ONTOLOGY, OWL.versionInfo, Literal("1.0-source-faithful")))
    g.add((ONTOLOGY, RDFS.comment, Literal("现有接口：7个输入、1个数据输出；预期效果不等于现场事实。", lang="zh")))
    for name, label in CLASSES.items():
        g.add((S[name], RDF.type, OWL.Class))
        g.add((S[name], RDFS.label, Literal(label, lang="zh")))
    for child, parent in [("AtomicSkill", "Skill"), ("CompositeSkill", "Skill"),
                          ("RobotArm", "Resource"), ("Gripper", "Resource"), ("Camera", "Resource")]:
        g.add((S[child], RDFS.subClassOf, S[parent]))
    for a, b in [("AtomicSkill", "CompositeSkill"), ("Skill", "SkillInvocation"),
                 ("TaskProcedure", "Skill"), ("CartesianPose", "JointConfiguration"),
                 ("ExecutionRecord", "SkillInvocation"), ("SkillCondition", "Observation")]:
        g.add((S[a], OWL.disjointWith, S[b]))
    properties = {
        "hasStep": (["CompositeSkill", "TaskProcedure"], "SkillInvocation", "包含步骤"),
        "inProcedure": ("SkillInvocation", ["CompositeSkill", "TaskProcedure"], "所属流程"),
        "invokesSkill": ("SkillInvocation", "Skill", "调用技能"),
        "hasInput": ("Skill", "SkillParameter", "具有输入"),
        "hasOutput": ("Skill", "SkillParameter", "具有输出"),
        "hasBinding": ("SkillInvocation", "ParameterBinding", "具有参数绑定"),
        "bindsParameter": ("ParameterBinding", "SkillParameter", "绑定参数"),
        "sourceParameter": ("ParameterBinding", "SkillParameter", "来自复合技能输入"),
        "sourceData": ("ParameterBinding", "DataReference", "读取数据"),
        "targetData": ("ParameterBinding", "DataReference", "写入数据"),
        "producesData": ("SkillInvocation", "DataReference", "产生数据"),
        "exportsData": ("SkillParameter", "DataReference", "导出局部数据"),
        "parameterData": ("SkillParameter", "DataReference", "接口对应局部变量"),
        "implementedBy": ("Skill", "SkillImplementation", "实现为"),
        "hasFixedConfiguration": ("SkillImplementation", "ImplementationConfiguration", "固定实现配置"),
        "hasFixedBinding": ("ImplementationConfiguration", "ParameterBinding", "包含固定绑定"),
        "hasPrecondition": ("Skill", "SkillCondition", "前置条件"),
        "hasExpectedEffect": ("Skill", "SkillCondition", "预期效果"),
        "hasApplicabilityCondition": ("Skill", "SkillCondition", "适用条件"),
        "usesResource": (["SkillInvocation", "Task"], "Resource", "使用设备"),
        "usesProcedure": ("Task", "TaskProcedure", "使用任务流程"),
        "actsOn": ("Task", "Workpiece", "操作对象"),
        "hasDestination": ("Task", "PlacementSlot", "目标放置位"),
        "recordsInvocation": ("ExecutionRecord", "SkillInvocation", "记录调用"),
        "hasEvidence": ("ExecutionRecord", "Observation", "具有观测证据"),
        "observesEntity": ("Observation", "Thing", "观测对象"),
        "hasPartType": ("Workpiece", "PartType", "零件类型"),
        "hasCarrier": ("Workstation", "Carrier", "工位载具"),
        "hasSlot": ("Carrier", "PlacementSlot", "载具放置位"),
        "forPartType": (["PerceptionProfile", "ReadinessEvidence"], "PartType", "适用零件类型"),
        "forSlot": (["PlacementProfile", "ReadinessEvidence"], "PlacementSlot", "适用放置位"),
        "hasPerceptionProfile": ("PartType", "PerceptionProfile", "感知配置"),
        "hasPlacementProfile": ("PlacementSlot", "PlacementProfile", "放置配置"),
        "expressedInFrame": ("CartesianPose", "CoordinateFrame", "表达坐标系"),
        "fromFrame": ("CalibrationTransform", "CoordinateFrame", "源坐标系"),
        "toFrame": ("CalibrationTransform", "CoordinateFrame", "目标坐标系"),
        "hasTargetPose": ("PlacementProfile", "CartesianPose", "机器人目标位姿"),
        "hasCaptureJoints": ("PerceptionProfile", "JointConfiguration", "拍照关节位"),
        "hasPortContract": ("SkillImplementation", "PortContract", "原始端口定义"),
        "mapsToPort": ("SkillParameter", "PortContract", "对应端口契约"),
        "derivedFrom": ("Thing", "SourceDocument", "来源文档"),
        "forImplementation": ("ReadinessEvidence", "SkillImplementation", "适用实现"),
    }
    for name, (domain, range_, label) in properties.items():
        g.add((S[name], RDF.type, OWL.ObjectProperty))
        g.add((S[name], RDFS.domain, union(g, domain)))
        g.add((S[name], RDFS.range, union(g, range_)))
        g.add((S[name], RDFS.label, Literal(label, lang="zh")))
    # Shared provenance fields intentionally have no narrow domain.
    data_properties = {
        "confirmationStatus": "确认状态", "sourcePath": "来源XML路径", "sourceFile": "来源文件",
        "sha256": "源内容摘要", "xmlTag": "XML节点标签", "treeId": "树标识",
        "treeOrder": "源文档树次序", "xmlName": "原始name", "nodeKind": "节点类别",
        "stepIndex": "步骤序号", "parameterName": "参数名称", "direction": "参数方向",
        "semanticType": "语义类型", "cppType": "原始C++类型", "declaredDefault": "声明的默认值",
        "defaultPresent": "是否声明默认值", "embeddedDefault": "内嵌模型默认值",
        "embeddedDefaultPresent": "内嵌模型是否声明默认值", "description": "说明",
        "bindingDirection": "绑定方向", "literalValue": "字面量", "rawBinding": "原始XML绑定",
        "scope": "数据作用域", "dataKey": "数据键", "interfaceOrigin": "接口来源",
        "isPublicConfig": "允许外部配置", "conditionKind": "条件类别", "checkMechanism": "检查机制",
        "ruleId": "校验规则编号", "sourceModelXML": "源节点模型", "embeddedModelXML": "内嵌模型快照",
        "rootAttributes": "根元素属性", "sourceNodeXML": "源调用元素",
        "alias": "业务别名", "objectName": "识别对象标识", "modelName": "识别模型标识",
        "threshold": "原始阈值", "exposureTime": "原始曝光参数", "rawValue": "原始序列化数值",
        "unitStatus": "单位确认状态", "frameStatus": "坐标确认状态", "lengthUnit": "长度单位",
        "angleUnit": "角度单位", "orientationRepresentation": "姿态表示",
        "evidenceKind": "证据类别", "evidenceReference": "证据引用", "sourceHash": "适用源版本",
        "validUntil": "有效截止时间", "observedAt": "观测时间", "observedState": "观测状态",
    }
    for name, label in data_properties.items():
        g.add((S[name], RDF.type, OWL.DatatypeProperty))
        g.add((S[name], RDFS.label, Literal(label, lang="zh")))
        datatype = XSD.integer if name in ("stepIndex", "treeOrder") else XSD.boolean if name in ("defaultPresent", "embeddedDefaultPresent", "isPublicConfig") else XSD.string
        g.add((S[name], RDFS.range, datatype))
    return g
