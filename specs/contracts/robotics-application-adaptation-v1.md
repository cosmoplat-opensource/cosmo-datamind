# 机器人抓移放应用适配 v1 — DR-072 / IR-026 T01

日期：2026-11-12。状态：T01 与 T02 核心调用面已确认；T03 纯核心已实现并回归通过；T04 测试与第 12 节调用面已确认，T05 集成实现和全量回归通过，待 T06 实际副本验收。
依据：[DR-072](../decisions/DR-072-robotics-application-adaptation.md)、[IR-026](../iterations/IR-026-robotics-application-adaptation.md)、[已交付公共协议](application-contracts-v1.md)。

## 1. 范围和主流程

构建阶段不预置应用类别，沿用通用 TBox＋ABox 和 LLM／离线路径，完成后直接入库。
只有用户选择行为树应用时才检查：TBox 角色与语义 → 执行事实 → 当前任务业务信息 → 所选模式依据。
第一版仅适配现有 UR 固定抓移放；支持源 XML 的单层 Sequence、Action／Condition／SubTree，以及已支持的 SetBlackboard。
其他控制节点、任意流程规划、新硬件驱动和现场机器人执行不在范围内。

流程：原固定版本 → 只读诊断 → 用户显式选择修订／适配 → 新派生版本 → 复检 → 进入或返回原任务 → 补业务信息 → 生成前复核。
不兼容候选仍可浏览和下载；检查不改写本体、不运行模型或提取，不自动创建业务对象。
已有 IR-025 初始检查可返回 execution.consumer_incompatible；只有本轮新检查器取得可定位证据后才返回精确缺项。

## 2. 当前断点与复用位置

| 位置 | 已有能力 | 本轮增补 |
|---|---|---|
| modeling/_extractor.py、data/xml_mapping.json | 确定性调用／绑定提取、scope、固定配置、部分感知配置、遗漏与来源报告 | 应用专用的执行字段补充、角色映射、静态值追踪及覆盖，不改变通用提取默认范围 |
| modeling/evidence.py、abox.py | 源事实与确认摘要验证 | 固定来源和规则版本，拒绝过期确认／冲突来源 |
| modeling/export.py | 原设计 TBox/ABox RDF 导出 | 应用派生版本导出及自包含执行视图；不修改原 v1 字节 |
| semantics.py | 从 RDF 恢复流程、模型端口及公开配置位置 | 尽量原样复用；先满足其消费字段和身份约定 |
| consumer.py、artifact_contract.py、compiler.py、validator.py | UR v2 SHACL／结构／数据流校验、仅修改公开配置 | 保留最终守卫；不能只添加 artifactContract 标记通过 |
| task_dialogue／task_completion／task_knowledge／grounding | 对象澄清、配置关联、原始值确认和任务补全 | 接入新派生版本，继续保存新版本并保留原任务 |
| application_contracts | 报告、幂等、修复分发和版本守卫 | 注册领域检查器与真实修复处理器 |

确认的断点：现有映射不包含 treeId、treeOrder、xmlTag、xmlName、rootAttributes、isPublicConfig 等完整执行信息。
原建模结果使用自选 namespace／individual IRI；旧消费者还依赖固定标准身份与发布规则，两者不能直接互换。
旧配置来源包含示例业务对象；本轮不能复用其 add_domain 来制造新本体的示例工件／工位。
TaskService 读取经 GovernanceService._graph，后者会读取 source_asset_ids 的文件字节；因此仅证明编译器不读 XML 不足以满足 RAC-05，任务准备和补全链路也必须处理此依赖。

## 3. TBox 角色与检查范围

角色映射使用术语的完整 IRI、实体种类及映射版本，不能按中文名／短名相似自动认定。
固定词表中的规范 IRI 可按已验证规则登记；其他对应需要用户明确确认，并校验父类、方向和属性范围。
不能将 AtomicSkill 与 SkillInvocation、SkillParameter 与 ParameterBinding、接口输入与局部数据混为一类。
TBox 类已声明不等于已有实例；缺类与缺实例分别定位。

| 使用阶段 | 类别／角色 | 必需关系或说明 |
|---|---|---|
| 准备执行 | Skill、AtomicSkill、CompositeSkill、TaskProcedure | implementedBy、hasStep、invokesSkill；原子／复合／流程分工与源结构对应 |
| 准备执行 | SkillImplementation、PortContract、SkillParameter | hasPortContract、hasInput、hasOutput、mapsToPort；唯一方向、类型和默认值声明 |
| 准备执行 | SkillInvocation、ParameterBinding、DataReference | hasBinding、bindsParameter、sourceParameter／sourceData／targetData、parameterData；每个绑定来源互斥 |
| 存在内部固定输入时 | ImplementationConfiguration | hasFixedConfiguration、hasFixedBinding；保留内部常量，不变成公开可编辑参数 |
| 来源与规则追溯 | 来源角色、原设计引用、规则与映射依据 | 存入血缘／证据，不要求用户把全部平台元数据建为业务类别 |
| 当前任务需要时 | Workpiece、PartType、Workstation、Carrier、PlacementSlot | hasPartType、hasCarrier、hasSlot；由用户选择或明确创建 |
| 当前任务需要时 | PerceptionProfile、PlacementProfile、JointConfiguration、CartesianPose | hasPerceptionProfile、hasPlacementProfile、hasCaptureJoints、hasTargetPose；不能用普通 ParameterBinding 替代业务配置 |
| strict 或已有有效依据时 | ReadinessEvidence 及适用范围 | forImplementation、forPartType、forSlot；沿用有效期、审核及撤销语义 |

Task、Resource／设备、SkillCondition／坐标等按所需角色和现有 UR 规则处理，不因类别名缺失就宣称全部业务知识缺失。
固定发布器需要的规则／预期条件由版本化应用配置提供，只表示规则，不制造设备身份、校准值或现场观测。
没有当前任务时，业务角色／实例检查可 not_evaluated；不能因此把整个本体标为 ready。
应用修复中若需要新增用户领域类别或改变关系范围，先展示 TBox 修订供确认；不能在通用构建或只读诊断中隐式注入。

## 4. 执行事实、来源和公开配置

| 内容 | 所需字段／事实 | 提取依据与拒绝条件 |
|---|---|---|
| 根与流程 | rootAttributes、treeId、treeOrder | 主树与 BehaviorTree 文档顺序；重复／缺失 ID、主树无效均阻塞 |
| 调用 | stepIndex、xmlTag、可选 xmlName、被调用技能 | 源节点与所属流程；SubTree 的节点标签和目标 ID 分开保存，不能把技能名当 XML 标签 |
| 端口 | parameterName、direction、cppType、defaultPresent／declaredDefault | 原声明或明确允许的内建接口；冲突、未声明默认值、类型不确定阻塞相关执行映射 |
| 参数绑定 | bindingDirection 和唯一 literalValue／sourceParameter／sourceData／targetData | 输入常量、父接口、局部读取与输出写入分开；保留 rawBinding 及来源位置 |
| 数据引用 | scope、dataKey、父接口对应、生产者／消费者 | scope 与完整调用路径参与身份；同名变量跨流程不合并 |
| 固定实现配置 | 内部常量及对应绑定 | 源调用与实现；不从样例填值、不开放其余内部字段 |
| 公开配置 | isPublicConfig、准确 binding／调用位置 | 固定应用规则限定下列六项；必须有唯一、可追溯的可写配置位置 |

公开项沿用现有配置接口：Perception 的 position、exposure_time、model_name、threshold、object_name，以及 Place 的 place_pose。
grasp.obj_in_robot 是执行数据流输入，不是用户可以填常量覆盖的公开配置。
编译器只允许公开项变化；节点顺序、技能／端口契约、内部算法常量、坐标转换和设备接口保持受约束。
数值保留原始序列化字符串；单位、坐标系、姿态排列和适配状态不从数值形状推断。

SetBlackboard 使用显式内建规则，区分 value 输入与 output_key 产生的局部数据，不把输出键当业务对象。
变量只在完整 scope／调用绑定下存在唯一且在使用前可达的静态赋值时解析为配置值。
多生产者、循环／递归依赖、动态计算、输出回流、跨 scope 未映射、缺赋值、类型冲突均保留缺项，不按最后出现的字符串猜值。
对已有 sourceParameter 与 parameterData 的对应分别追踪；不能把父接口名同时当作任意局部变量。
缺少或损坏原始来源时，不允许重提取／适配伪造事实；保留已有设计及其历史结果。

## 5. 三类修复与回到原任务

| 类型／action_id（拟定） | 适用问题 | 用户动作及结果 |
|---|---|---|
| robotics.revise_tbox | 缺应用角色或属性语义不兼容 | 打开父版本对应的设计修订，明确确认类别／关系／属性；重新提取，形成派生版本 |
| robotics.adapt_execution | 缺执行字段／静态映射 | 展示角色映射、来源和提取影响，经明确提交进行确定性适配；冲突无法自动修复时只给定位 |
| robotics.complete_task | 已有执行结构，缺业务对象／关联／值 | 返回原任务表单，复用已有澄清／配置／确认流程；不能用此入口补造技能顺序 |

沿用 POST /api/application-checks/<check_id>/repairs，不新增应用选择步骤。
所有动作目标来自报告中已登记的 issue/action，客户端不能替换任意资源／函数或提交 ready。
返回上下文保留原任务 session_id、固定父版本和检查引用；产生新版本后，必须经显式确认切换任务所用版本并复检。
expected_revision 对应实际修订资源：已有设计会话使用设计修订，原任务使用任务 revision，尚无会话的资产修复使用独立适配记录 revision。
资产版本 ID、check_id 与 revision 不互换；独立适配记录需在实现时采用增量存储，并沿用公共请求幂等和事务受理协议。
导航只打开界面；submit 在事务内登记持久运行，不在事务中调用模型或长时间提取。
重复请求只受理一次，过期报告／父版本／目标修订变化均返回冲突，不悄悄切换资产或覆盖别的分支。

## 6. 应用派生版本与单一事实来源

应用适配显式产生同资产的新固定版本，parent_version_id 指向实际适配的原版本，候选状态不自动发布。
在父版本之上另有分支时，复用工作头 CAS；不能无提示覆盖并发用户修改。
记录角色映射、原实例→执行身份映射、提取规则／词表／来源摘要、父版本及修复运行。
执行身份可按现有 S 命名空间规范化以复用消费者，但不能仅基于同名生成 owl:sameAs，也不能借用原 add_domain 的示例实体。

原设计仍在父版本及血缘快照中逐字节保留；扩展知识在派生结果中保留来源和对应关系，不被固定执行结构静默丢弃。
涉及同一业务配置的原实例与标准执行身份不能成为两份独立可编辑权威值。
应用版本的当前事实以当前有效 RDF 为准；原设计快照只作为历史依据，修改入口按身份映射定位当前配置。
同一次任务补全改变当前配置时，形成新应用版本并同步完整 JSON；不能只改执行投影、留下另一份“当前”旧配置。
从修订后的 TBox 再适配时重新检验映射和来源；不沿用旧 task_hash／旧通过报告。

## 7. 工件与旧消费者兼容

新增工件标识为 {"id":"robotics-application-design","version":2}，与旧 {"id":"robotics-design","version":1} 明确分离。
消费者执行 RDF 继续使用受信 ur-pick-place-owl-v2 及其内嵌规则，不创建一个只放宽校验的新消费者方言。
只有结构、端口、数据流、来源映射和既有规则通过后，才输出对应的合法执行表示；只打 artifactContract 标记必须失败。
准备阶段允许既有 inspect_structure 所认可的业务可补全缺口；不能把业务可补字段的例外扩大到缺技能／顺序／绑定。
生成阶段仍走现有消费／编译／校验和 offline／strict 分支，完整结果不接受“仅准备结构可用”的报告替代。

对外下载仅要求 candidate.owl 和一个完整 design.json；manifest 可继续作为内部或审计工件。
完整 JSON 的必备内容：

- 工件 schema／版本、固定 output_asset_ref、父版本、实际 OWL 字节摘要和组件／规则版本。
- 当前有效 TBox、个体、对象／数据断言；能独立恢复当前 RDF 语义，而非只装原设计快照。
- 原设计／确认／消息／来源证据与原始版本引用、角色及实例身份映射。
- 执行事实及配置来源、遗漏／冲突／覆盖、用户补全记录和当前验证摘要。
- 应用检查引用及检查时依据；历史报告不可伪装成当前执行许可。

旧 v1 OWL／JSON／manifest 及下载地址不就地重写；新读写按工件标识明确分派，未知版本拒绝。
原生旧 UR 版本继续走原流程；新适配不能伪称原始 legacy_xml 构建。
JSON／OWL 同一事务可见、版本和摘要一致；任务补全、TBox 修订、适配重试均不得留下半套工件。
下载 JSON 的当前事实与对应 OWL 应 RDF 同构；总 JSON 自包含，不要求用户拼接几个业务文件。

## 8. 自包含生成与证据边界

提取／适配阶段需要来源完整性；来源损坏给出源角色、文档摘要和节点位置。
成功的应用版本须自包含执行结构、端口、绑定、公开配置位置与当前业务事实。
之后任务准备、补全、应用复核和生成都不能读取原 XML 来兜底恢复执行信息。
实现需分开“历史来源可审计”与“当前应用载荷可执行”的读取边界，修正现有 GovernanceService._graph 对全部原始来源的文件依赖。
仍保留来源 ID、摘要及历史引用；移走原 XML 不授权忽略应用 OWL／JSON 损坏，strict 所需证据及撤销记录也不能绕过。
旧设计历史在来源文件缺失时可以报告来源完整性问题；这不应导致已自包含的新应用版本退回 XML 生成。

offline 延续可选适配依据／独立未验证状态；strict 要求当前范围的有效证据、确认、有效期和撤销检查。
不从“仿真／离线候选已生成”推导“现实设备执行已验证”；real/demo 模式不混用。

## 9. 稳定诊断与 TDD 计划

拟定代码以所属层和来源事实命名；保留 IR-025 的 error／missing／unsupported／invalid 区分。

| 诊断代码族 | 定位证据 | 补足方向 |
|---|---|---|
| schema.missing_role / schema.role_conflict | 完整术语 IRI、种类、domain/range 与映射 | TBox 修订或角色确认 |
| execution.missing_metadata / execution.missing_order | 流程／调用和具体字段、源节点 | 重提取／适配 |
| execution.binding_ambiguous / execution.type_conflict | scope、生产者／消费者、端口 | 来源修正或显式映射；不猜值 |
| execution.unsupported_template / execution.source_integrity | 不支持的控制结构／来源摘要 | 说明边界或修正来源 |
| task.missing_entity / task.missing_profile / task.missing_value | 当前对象、关联或配置字段 | 原任务补全 |
| environment.evidence_invalid | 当前实现／对象／槽位范围与撤销／有效期 | 原依据确认流程 |

T02 先写核心 RED：角色语义、树／步骤、端口方向／类型、scope、静态赋值、非公开参数保护、来源冲突、身份映射、扩展知识保留和工件一致性。
T04 写集成 RED：点击才检查、三类修复导航与 CAS／幂等、原任务返回、无示例业务对象、配置改动后的总 JSON 同步、移走原 XML 后的完整任务链路。
新旧对照以同一 XML、相同明确业务对象及配置为输入：比较 XML 节点／调用顺序、端口方向、非公开参数和六项公开值及参数来源；不强求序列化空白或随机实例 IRI 相同。
来源／XML 样例、冲突、缺业务事实、无有效证据、real/demo 和 offline/strict 均列边界；不得用合成通过代替真实本体副本的最终验收。
RAC-01～05 criterion 不改写；T02 已按实际创建测试填写核心函数引用，T04 再补集成覆盖。

## 10. 本次评审结论入口

需用户审阅：上述角色与执行清单、三类补全分工、规范身份与当前事实边界、新工件标识及旧 v1 不改写、移走源 XML 后的全链路验证。
确认后冻结本规约并进入 T02 RED；本次不修改产品实现、不创建未获评审的成功假实现，也不启动真实用户本体修复。

## 11. T02 核心测试调用面（随测试提交人审）

目标门面为 `domains.robotics.application_adaptation`，内部可拆分模块；本轮不创建占位实现。
本节只确定纯核心的数据调用面，不包含数据库、修复服务、HTTP 或页面。

- `resolve_roles(tbox=..., role_map=..., required_for='execution'|'task')` 返回 status、issues 和按实体种类分组的 roles（角色→完整 IRI）。
- `extract_execution(confirmation=..., evidence_bundle=..., role_map=...)` 返回 status=extracted、issues=[] 及 execution，或 status=blocked、问题列表和空 execution。
- `adapt(design=..., role_map=..., mode='real'|'demo')` 显式执行纯适配，返回 status=adapted、ontology bytes、identity_map、当前事实及规则／覆盖信息，或 blocked 且无部分 ontology/document。
- `export_bundle(product, output_asset_ref=..., parent_version_id=...)` 返回 `{owl:bytes,json:bytes}`；服务日后负责分配真实版本和事务持久化，纯函数不建库／发布／写文件。
- `restore_bundle(json_bytes)` 仅从完整 JSON 恢复 RDF/XML bytes；未知工件版本、内容／摘要不一致抛 ValueError 派生错误，code=application_artifact_invalid。

role_map 为可信内部的版本化确认记录，含 version、tbox_sha256、confirmed、bindings。
每个 binding 使用 kind（classes/object_properties/data_properties）、role、iri；confirmed=false 或摘要过期不能使用。
该内部 bool 不等于 HTTP 授权凭据；真实确认状态须由后续服务保存并核对 revision，不能信任客户端直接传 confirmed=true。

design 核心输入为已在宿主验证并解析后的 tbox、confirmation、abox、coverage、evidence、ontology bytes 和 origin。
origin 含 ontology_id、version_id、payload_sha256，必须对应传入的实际本体字节。
它不是完整持久化 v1 design.json 的替代验证器；T04 再覆盖 v1 运行／工件一致性到该内部结构的可信解析。
扩展知识核心夹具在原 RDF 中加入一项明确标注的合成注释并更新输入摘要，只验证 RDF 扩展保留，不代表已经验证真实资产修改授权。

execution 最小结构：

- root_attributes：XML 根属性。
- trees：按源文档顺序，含 tree_id、tree_order（从 1 开始）、calls。
- call：id、step_index（从 1 开始）、xml_tag、可空 xml_name、skill_id、source_refs、bindings。
- binding：id、parameter、direction、kind、value、scope、source_refs；kind 为 literal／parent_parameter／local_input／local_output。
- data_references：id、scope、key；ports：owner、parameter、direction、cpp_type、default_present、可选 declared_default、source_refs。
- public_configs：skill、parameter、value、binding_id、invocation_id、source_refs、confirmation_status=source_extracted、unit_status=unconfirmed。

问题包含 code、layer、message、expected、observed；schema 角色缺项另有 kind／role，XML 问题使用有效 source_refs 定位。
来源完整性失败不要求捏造一个不存在的有效源节点引用；可在更外层使用来源角色／摘要诊断。
identity_map 含 role、source_iri、target_iri、source_refs，不依赖图谱显示名。
完整 JSON 含 contract、output_asset_ref、parent_version_id、payload_sha256、current.tbox／abox、origin.asset_ref／design_snapshot、identity_map、role_map、coverage、validation、components.adapter_version、execution_readiness。
具体 RDF 当前快照编码可在 GREEN 内实现，但必须自包含并交叉校验当前事实与 OWL；不得只靠内嵌 OWL 副本忽略 current 被篡改。
恢复时不读取输入旁的 sidecar／原 XML；允许读取随代码发布的可信规则资源进行校验，不访问网络。
该 pure bundle 不提供并发发布、版本 CAS 或任务补全同步保障；这些必须在 T04/T05 集成测试中验证。

## 12. T04 集成测试调用面（随测试提交人审）

本节把已确认流程落到可执行接口，不改变 RAC-01～05 或第 11 节纯核心接口。
本节随 T04 测试获用户确认；以下服务、路由、修复按钮和 v2 持久化已在 T05 实现，57 项集成测试与全量回归通过。
2026-11-13 用户确认自动匹配与生成，以下公开准备／修复和人工提交约定按该新决定更新；底层分步方法保留兼容。

### 12.1 适配服务和持久状态

`domain_runs.robotics_application_service.ApplicationService(root, read_only=False)` 复用真实资产、RunStore 及事务，不接受客户端执行器或完成状态。

| 方法 | 输入与行为 |
|---|---|
| prepare | ontology_id、version_id、mode、可空 task_ref={session_id}、request_id；保存独立记录与固定输入／工作头；公开及修复入口传服务端 automatic=True，通过规范匹配后自动受理运行；默认低层分步调用仍只准备记录 |
| get | adaptation_id；只读恢复固定记录，不自动提取、提交或迁移数据库 |
| confirm_roles | adaptation_id、bindings、expected_revision、request_id；按真实 TBox 校验并保存确认，生成可信 role_map，而非信任客户端 confirmed／schema_hash |
| submit | adaptation_id、expected_revision、request_id，可选 bindings；携带 bindings 时在同一事务中确认并入队，否则沿用已确认映射；失败后的新 request_id 为显式重试并关联 retry_of |
| execute | run_id；可信工作进程调用，返回真实 RunStore 终态；不暴露为 HTTP 执行任意任务接口 |
| rebase | adaptation_id、asset_ref、expected_revision、request_id；显式绑定同资产重新确认的设计版本，清除旧确认／运行状态，重新诊断 |
| resume_task | adaptation_id、expected_revision、expected_task_revision、request_id；显式将原 session 切换到新固定版本，清除旧候选并复核，不能静默创建另一个任务 |

状态含 adaptation_id、asset_ref、mode、task_ref、revision、status、role_proposal、role_map、issues、run_id、output_asset_ref。
prepare 的 revision=0，role_map／run_id／output_asset_ref 为空；角色齐全时 awaiting_role_confirmation，缺项时 needs_tbox_revision。
上一条描述低层分步准备；自动路径校验通过后直接返回 queued，role_map.confirmed=true，并记录 matching.method=automatic 和固定规则／词表摘要。
自动对应要求相同实体种类、规范 ID、已知受控词表及完整语义校验通过；未知名称只进入 term_options，由人工选择。
role_proposal.confirmed=false，预选本身不授权；可信映射只能由固定自动规则通过或用户提交的整份映射经后端校验后形成。
提交与执行完成分别为 queued／completed；失败不给 output_asset_ref 或半套版本／工件。
同 request_id 同输入重放原响应且不写新运行；同 ID 异输入为 request_conflict。
旧修订、未确认、并发工作头变化分别为 revision_conflict、confirmation_required、head_conflict。
三次并发重复提交仅产生一个运行；提交后执行前输入损坏返回 execution.source_integrity，工作头变化不覆盖新分支。
版本写入失败回滚版本和文件，运行可保留失败事实；显式重试可继续，不通过刷新自动重跑。
原任务发生并发修改时 resume_task 返回 session_conflict；重放同一已成功返回操作不再次改任务。
只读缺根返回 not_found，旧 schema 返回 storage_unavailable，均不建库／迁移；显式可写升级保留全部旧字段值、工件字节及外键完整性。

### 12.2 HTTP、报告和修复导航

- POST `/api/robotics/application-adaptations`：尝试自动匹配，通过后返回 queued／202；需要人工核对则返回准备记录／201。
- GET `/api/robotics/application-adaptations/<id>`：只读状态，200。
- POST `/<id>/confirm-roles`：只接收 bindings、expected_revision、request_id，200。
- POST `/<id>/submit`：接收 expected_revision、request_id 及可选 bindings，202；bindings 的每项只含 kind、role、iri，客户端授权字段均拒绝。
- rebase／resume_task 供页面接点复用服务；HTTP 若暴露，必须沿用同一固定引用、CAS 和幂等边界。

新路由复用宿主 Host／Origin 守卫；revision 必须为非负整数，拒绝 bool、字符串、负数和 null。
客户端注入 confirmed／ready／payload／executor／schema_hash 等权限或结果字段，400 invalid_request，且无写入。
功能关闭时只读不存在记录返回 JSON 404 not_found，提交为 503 feature_disabled，不初始化存储。
固定版本详情和旧下载地址按工件版本分派；v2 详情的 application_design 标明 contract，JSON／OWL 下载对应同一版本。
`ontology_assets.robotics_application.ApplicationAssets(root, read_only=True)` 的 payload／export_json 验证已存总 JSON 与 OWL；篡改返回 409 artifact_integrity，不影响旧 v1 下载。

现有 POST `/api/application-checks` 使用真实领域检查器定位 schema.missing_role、execution.missing_metadata 及具体 task 缺项；问题有实体／证据引用。
检查无模型调用，不新增来源、本体版本或运行，允许新增报告与请求记录。
`robotics.revise_tbox` 导航原设计 session 并保留返回上下文，导航本身不执行 edit_classes。
`robotics.adapt_execution` 在显式修复请求中尝试自动匹配并入队；需要人工核对时不创建运行。
repair 保持 kind=navigate，target.resource_kind=robotics_adaptation、resource_id=adaptation_id；随后 GET 只恢复实际进度，不发起新生成。
`robotics.complete_task` 导航同一 task session，复用原缺项表单，不重新解析任务或制造业务对象。
修复仍只接受报告登记的 issue/action；未知动作拒绝，真实角色确认改变适配依据后，旧报告不得作为当前许可。

### 12.3 浏览器接点与完整任务链

通用构建类别／关系／属性确认完成后直接进入本体库，应用检查表为空，无应用选择或自动适配请求。
检查页面仅在用户点击应用按钮后出现；可用修复按钮以 data-action-id 绑定后端动作，界面文字使用业务说明。
独立适配页沿用 `#robotics-task`，携带 adaptation 参数；panel 的 data-testid=robotics-application-adaptation，并显示 data-adaptation-id、data-status。
角色对应使用可核对的术语选择（测试示例的可访问名称为“对应术语：ParameterBinding”），不让用户手输摘要或执行器。
自动通过时只展示生成进度与结果，不显示人工确认／生成按钮；无法确定时展示对应选择，一次“确认并生成应用版本”提交整份映射。
刷新与独立宿主重启只读恢复；完成后“查看应用版本”进入版本详情，可下载完整 JSON／OWL，再经复检进入原任务界面。
缺类修复返回原设计；业务修复返回同一任务，保留载具／槽位／感知配置等已有“关联对象”“新建关联对象”“保存并继续”交互。

任务链测试调用真实 TaskService.choose／configure／complete、版本保存、grounding、编译器和校验器；仅脚本化外部模型传输。
每次对象创建、关联、参数、确认保存后，固定版本总 JSON 可独立恢复与 OWL 同构的当前事实；旧版本和 origin.design_snapshot 不被改写。
感知阈值明确改为 0.91 后，与相同配置的旧路径比较 XML 结构／属性及六参数来源，不比较序列化空白。
分别在任务开始前和补全后移走临时测试根中的原双 XML，仍能准备、补全、再次生成及读历史，且不读取原 XML 兜底。
offline 无现场适配证据可以形成未验证候选；strict 缺有效依据不能生成，显式合成依据仅验证软件流程，execution_readiness 始终 not_verified。
real 原设计不能在适配时静默转 demo；保留原 demo 任务隔离规则。
本轮未替代 T06 的真实用户本体副本验收，也未验证任何现场机器人可执行性。
