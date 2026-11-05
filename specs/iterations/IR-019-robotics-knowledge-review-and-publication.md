# IR-019 · 第四阶段：机器人知识维护、重建合并与评审发布

- **状态 / Status**: in-progress
- **日期 / Date**: 2026-11-05
- **当前环节**：用户已确认 T04 覆盖；T05 HTTP 与下载接线实现完成，定向 158 项及全量十段通过；下一步 T06 UI/CLI 失败测试，IR-019 整轮仍在进行。
- **关联 / Refs**: [[DR-059-unified-domain-semantic-graph]]、[[DR-060-robotics-source-recipes-and-reverse-build]]、[[DR-061-parameter-contracts-calibration-and-readiness]]、[[DR-063-local-domain-registration-and-application-ui]]、[[DR-064-persistent-build-runs-and-artifact-manifests]]、[[DR-065-robotics-migration-and-compatibility-validation]]。
- **前置**：[IR-018](IR-018-robotics-build-api-and-ui.md) 已于 2026-11-04 经用户确认 delivered。
- **方案**：[集成 Proposal](../PROPOSAL-yang-datamind-integration.md) 步骤 4；步骤 5～6 的任务提取和行为树应用仍另立 IR。
- **流程依据**：[SDD skill](../../.agents/skills/sdd-workflow/SKILL.md) 第③～⑤步；本轮范围确认与各批测试覆盖确认分别记录。

## 目标 Goal（已确认）

用户从 IR-018 构建的固定候选版本进入知识维护，补齐业务对象关系、参数配置与依据，保存为同一资产的新候选版本。
用户可比较版本、审核指定版本并发布；发布复验实际 OWL 字节，不把缺少任务证据误报为结构不合法，也不把发布成功误报为任务或执行就绪。
新来源重新构建后，用户可把该来源候选与原资产中的人工知识进行显式合并，查看并解决冲突，避免人工修改丢失或旧证据沿用。
本轮同时交付领域服务、HTTP、UI 与真实浏览器联动；不能只交付后端 Python 接口。

2026-11-05 用户回复“可以继续下一步了”，授权从已交付的 IR-018 进入下一阶段规划。
2026-11-05 用户了解映射、差异/合并及 profile 区别后要求“相关术语也同步更新到IR中，然后继续下一步”，据此确认本轮 Goal/Acceptance 并授权 T02；跨阶段 DR 仍保持 proposed。

### 统一术语

| 界面/文档名称 | 技术标识 | 含义 |
|---|---|---|
| 构建模板 | profile / profile_id = ur_pick_place | XML 导入时使用的流程识别、领域解释和支持约束，当前固定 UR 抓移放规则 |
| 感知参数配置 | PerceptionProfile | 模型名称、阈值、曝光时间、拍照关节位等具体参数集合 |
| 放置参数配置 | PlacementProfile | 指向具体放置目标位姿的参数集合 |
| 业务映射 | hasPartType / hasCarrier / hasSlot / hasPerceptionProfile / hasPlacementProfile | 业务对象之间、对象与参数配置之间的关联；建立关系本身不是适配证据 |

API、RDF 类与字段标识不随文案改名；后续界面禁止把上述三类概念统称“profile”。
当前“构建模板”标签改名及相关已审浏览器定位文本同步属于用户明确要求的术语调整；测试逻辑、断言数量和业务条件不变。

### 代码事实与接入方向

| 当前事实 | 本轮接入方向 |
|---|---|
| AssetStore.add_version 已保存不可变 RDF/XML，parent_version_id 只验证同资产；没有工作头、并发条件或审核记录 | 增加治理事务服务、候选工作头/修订号、请求去重和追加式审核记录；保存总是生成新版本 |
| publish_version 校验 consumer 后更新发布状态，没有署名、理由、所审差异或证据范围 | 领域发布服务在同一事务核对精确版本/摘要和审核记录，再登记发布；保留既有内部 API 兼容性 |
| 候选载荷只验证 RDF/XML 语法；consumer.validate_graph 检查协议/SHACL/模板，并允许未解决作者参数 | 分开“可保存的缺信息草稿”“结构可发布”“任务证据是否足够”；不要求草稿先具备全部业务知识 |
| grounding.resolve_request 已读取零件类型、工位/载具/槽位、参数配置和确认状态 | 表单操作映射到这些实际 RDF 关系；生成侧继续读取同一权威载荷，不能建立另一套编辑 JSON 真相源 |
| compatibility_confirmed 当前检查来源主 XML hash、类型/实现/槽位、确认状态、依据字符串和到期时间 | 新治理版本增加节点模型与受影响配置范围的证据约束、审核关联和撤销检查；不能直接把状态字符串改成 confirmed |
| invocation 身份由 tree_id/index 组成，步骤移动时同一索引可能代表不同调用 | 重建采用显式基线和语义前置条件；身份不明/顺序改变阻止自动重放，不按旧序号匹配 |
| 版本详情从 metadata.build_run_id 读取原构建 result/artifacts | 增加版本专属验证报告/下载登记；编辑版本不能下载原构建 OWL 冒充当前版本 |
| RunStore 现有构建/生成输入契约固定，旧 HTTP 运行入口只支持 build_ontology | 治理记录按资产/版本独立保存，不伪装成构建运行，不放宽旧运行输入或修改旧 manifest |
| UI 为原生 JS，library 固定版本详情已有路由；本体评审页主要是原数据功能 | 新增知识维护模块与机器人评审区，复用本体库/评审入口，保留原数据操作 |

更多证据及文件位置见 [本轮审计起点](../../docs/audits/IR-019-knowledge-validation.md)。

### 本轮边界与具体用户路径

```text
本体构建 → 候选详情 → 维护知识
  → 编辑业务关系 / 参数及来源 / 登记适配依据
  → 保存新候选 → 比较父版本 → 审核本版本 → 发布
  → 下载当前版本 OWL 和报告 → 刷新或重启后回查

新 XML 在原构建页生成另一来源候选
  → 原资产“合并新来源”选择该候选
  → 比较 原来源基线 / 当前人工版本 / 新来源候选
  → 逐项处理冲突 → 保存同一原资产的新候选 → 重新评审发布
```

| 本轮包含 | 本轮不包含，后续承接 |
|---|---|
| 零件、零件类型、工位、载具、槽位及其映射；感知/放置参数配置的受控维护 | 数据库记录级跨域映射、自动导入全部旧数据资产；它们不是机器人知识维护的前置条件 |
| 参数原值、依据、已知单位/坐标系；范围明确的适配证据登记、审核、撤销/替代 | 自动标定、凭点击证明实机适配、自动补齐缺失物理量 |
| 不可变候选、版本差异、审核、发布、重建冲突、真实版本工件 | 任意 RDF/SPARQL 写入、外部 OWL 导入、任意技能/端口/控制流编辑 |
| 表单、关系条目列表、参数/证据侧栏与差异/冲突界面 | 完整关系画布、行为树画布和独立数据流画布，仍由步骤 6 承接 |
| 复用原双 XML 构建作为新来源输入，显式选择并合并来源候选 | 一键覆盖原资产、自动猜测重命名或调用步骤对应关系 |
| 结构及发布校验；用已有严格生成内核验证门槛未退化 | LLM 模型接入、自然语言任务对话、行为树生成 API/UI、机器人执行 |

## 交付 Deliverables

- [ ] `domains/robotics/`：受控业务编辑、参数依据、适配证据范围、语义差异与合并校验；完整 RDF 权威载荷保留。
- [ ] `ontology_assets/`：治理服务、候选工作头/修订号、不可变版本关联、追加式审核/证据状态、幂等请求及版本工件；事务迁移与回滚验证。
- [ ] 发布服务：固定候选与报告、审核前置条件、消费契约复验、发布记录；保留旧内部发布/生成契约。
- [ ] HTTP：编辑 schema/投影、保存、差异、合并预览/提交、审核/发布、证据及版本下载；装配保护、并发/错误契约。
- [ ] UI：本体详情知识维护、版本比较、机器人评审、证据来源与失效提示、重建冲突、发布与下载，旧数据入口保留。
- [ ] 领域/持久化/HTTP/界面测试及连接实际 Gunicorn 和存储的浏览器验收；统一检查接线与旧能力回归。
- [ ] 可直接执行的离线知识维护演示、正常启动/隔离试用命令、实际浏览器操作指南及审计证据。
- [ ] acceptance 映射、Proposal/索引同步和最终交付对照；后续范围与人工验证边界明确标记。

## 领域与存储契约（本轮范围已确认）

### 1. 受控编辑与版本身份

前端编辑投影由服务端从固定 RDF 版本生成，提交有类型的操作，不提交替代整个 OWL 的 nodes/edges。
所有对象引用带资产/版本及完整 IRI；不按中文名或 local_name 自动等价，不跨资产隐式引用。
允许新增/维护 Workpiece、PartType、Workstation、Carrier、PlacementSlot 及其关系、PerceptionProfile、PlacementProfile 与关联关节/位姿配置；字段及关系由本地 schema 白名单定义。
名称/别名允许歧义存在并显示提示，不通过合并同名节点消除歧义；ID 冲突、错误关系类型、悬空引用和非法字段直接拒绝。
删除有引用对象须显式处理引用，禁止隐藏级联删除；所有未授权修改的三元组、数据类型、语言标签、空白节点与 RDF 列表保留。
原子/复合技能、调用次序、端口、绑定、条件、实现结构及协议/规则/来源摘要只读；公开业务参数配置可改，固定执行模板不可由本轮表单重排。

保存提交包含 base_version_id、base_payload_sha256、expected_head_version_id、expected_revision、request_id、操作者署名、理由和 operations。
基线与当前工作头分别表达：明确基于旧版本创建候选可以允许，但必须显示此选择并通过当前工作头并发检查。
工作头按资产事务递增；过期头/摘要返回 409，不丢弃页面未保存输入，也不自动覆盖新头。
同一 request_id 与同一输入重发返回原结果，不重复版本/审计；同 ID 改输入返回冲突。
旧资产首次写治理状态时，在事务中校验客户端看到的最新版本并初始化工作头；GET 不迁移或创建存储。
保存生成 candidate 及 parent_version_id，不修改基线 OWL；新候选不继承已审核结论。

### 2. 参数、证据与未知信息

感知/放置参数配置使用 grounding.profile_parameters 已有结构化字段与原接口字符串；不新增可独立修改的 profileValues JSON。
未知值、单位或坐标系允许留空并进入缺口报告；已填写值按端口及已知物理类型检查，不靠 LLM 或默认值推断事实。
人工补充的参数与映射带依据记录；原 XML 字面量只保留其来源事实，不自动升级为已标定参数。
证据支持本地文件登记和文字引用；文件保存逻辑 ID、摘要、大小，不接收服务器路径，不自动抓取用户填写的 URL。
证据至少记录 kind、实现、零件类型/目标范围、主 XML 与节点模型摘要、适用配置摘要、依据来源、有效期及审核记录。
登记和审核分开；审核要求范围、依据和署名，服务端记录时间及内容摘要，普通“保存/发布”按钮不能给全部实体或证据自动确认。
现平台没有完善的登录权限体系，署名是可追溯的人工声明，不宣称已核验人员身份，也不声称上传文档真实性经机器验证。

有效性在读取/生成准入时按当前时间判断，禁止无时区/无效期限；到期、撤销、替代和范围变化分别给出原因。
来源模型、实现、相关参数配置值或映射发生变化时，旧证据不得自动适用于新候选，必须重新审核所覆盖范围。
证据撤销采用追加记录，不重写旧 OWL/旧运行报告；平台从固定已发布版本生成时查询有效撤销记录并拒绝失效证据。
历史报告仍显示当时结果，另展示当前需复核提示；不得改旧 generated 结果冒充当时失败。
新治理版本声明服务端受控的证据策略版本，消费侧强制检查；历史 IR-016/017 载荷仍按原契约读取，不偷偷要求缺失的新字段。
旧契约与新治理策略的适用范围须出现在报告中；离线导出的 OWL 是当时快照，脱离平台不能获知之后的撤销，不能声称离线实时撤销已生效。
real/demo 不允许编辑转换；不调用 apply_demo_supplement 给真实版本补证据，模拟状态或 demo-only 引用不能作为 real 的新确认依据。

### 3. 差异、重建与冲突

普通版本比较先区分 RDF 语义变化与序列化变化；仅字节顺序或空白节点标号变化不得产生虚假的业务差异。
按实体/关系/参数/证据列出增删改，同时保留完整语义差异；未识别内容只读保留，不能投影后丢弃。
重建输入固定三者：可追溯的原来源基线 B、当前人工版本 L、通过原构建流程产生的新来源候选 R；另固定目标资产当前工作头和所有摘要。
R 可以来自另一构建资产，但必须用户显式选择、同受支持构建模板（profile_id）/mode，且有成功构建运行与来源快照可核对；不会自动合并目录中同名资产。
原始版本没有可证明的来源基线/修改来源时，阻止自动重建合并，展示缺失依据；不得猜测 B。
以 B→L 的人工改动和 B→R 的来源变化判定保留/更新/冲突；来源事实以新来源为依据，人工关系仅在身份和前置条件成立时重放。
同字段两侧不同修改、被删除对象上的人工修改、调用次序/身份变化、证据范围改变均须明确处理；不能按旧 stepIndex 静默套用。
用户选择保留人工、采用新来源或手工改值时仍受字段白名单和结构验证约束；证据失效不能通过“保留人工”强制恢复有效。
预览不写版本；提交固定 merge token、三份输入摘要、显式冲突决定和工作头，重算校验后事务创建新候选，过期预览返回 409。
新候选属于原目标资产，父版本为 L，记录 B/R/原构建引用及新来源摘要；B/L/R 原载荷与历史全部保留。
此阶段先复用现有构建页面生成 R，再选择合并；不改变 IR-018 的双文件上传和构建运行协议。

### 4. 评审、发布与版本工件

审核针对精确 version_id/payload_sha256，记录审核人署名、理由、接受/退回结论和当时结构/知识缺口报告摘要。
拒绝、未审核、过期审核或未解决合并冲突不得发布；并发编辑后旧审核不能授权新候选。
正式 UI 发布仅针对当前治理工作头，通过幂等请求核对审核与实际字节，再由受信 consumer 重验协议、SHACL、模板/端口/绑定。
缺少具体任务映射/适配证据可被报告并在显式审核后发布结构合格版本；发布不自动填写业务确认或任务就绪。
publication_state 从 candidate 到 published 是生命周期元信息变化；已保存 OWL 字节不变，封装需要改变载荷时先保存新候选并重新审核。
保留 AssetStore.publish_version 现有内部调用兼容；平台 HTTP 发布入口必须经过新的治理服务，不暴露绕过审核的接口。

版本工件单独登记，关联当前 version_id、载荷摘要、报告规则/策略版本和审核记录；提供当前 OWL、结构/知识报告及版本清单。
编辑版本可以保留 origin_build_run_id 供追溯，但不得把原构建 run 的 OWL/报告当成本版本的 OWL/报告。
旧构建版本原有下载 URL 和 RunStore manifest 保持可用；增加版本下载 API，并验证工件确实属于该资产/版本。
版本、头、审核/发布记录、报告索引和请求去重在一致事务中落库；故障不得留下可见的已发布状态而缺失工件/审核。
扩展 schema 4 时验证旧资产/构建/生成/来源/清单逐值保持；只读不升级，未知 schema 拒绝，迁移失败回滚。
采用本地有界操作及现有 RDF 锁，不新增分布式调度；解析和校验异常返回结构化问题，不能覆盖旧数据功能。

## HTTP 与 UI 接线契约（T04 固定）

下表固定 T04 的职责边界和测试契约；T05 实现，已有 IR-018 路由契约不删除或改义。

| 接口/模块 | 用途 |
|---|---|
| GET `/api/ontology-assets/<id>/versions/<version>/knowledge` | 固定版本可编辑投影、白名单 schema、工作头、策略和缺口 |
| POST `/api/ontology-assets/<id>/drafts` | 条件写入新的不可变候选 |
| GET `/api/ontology-assets/<id>/diff?base=<v>&target=<v>` | 同资产语义差异与字段变化 |
| POST `/api/ontology-assets/<id>/merge-previews`、`/merges` | 固定新来源候选的三方比较与冲突提交 |
| POST `/api/ontology-assets/<id>/evidence-sources` | 受限本地证据附件上传，返回来源 ID/摘要，不自动认可内容 |
| POST `/api/ontology-assets/<id>/evidence`、`/evidence-reviews`、`/knowledge-reviews` | 登记范围证据、审核证据、审核指定业务对象；每次生成新候选 |
| POST `/api/ontology-assets/<id>/versions/<v>/reviews`、`/publish` | 固定版本审核及发布 |
| POST `/api/ontology-assets/<id>/evidence-revocations` | 正文传完整 evidence_iri，追加撤销记录，保留旧快照 |
| GET `/api/ontology-assets/<id>/versions/<v>/payload` | 下载固定候选或发布版本自身的 OWL，不回退到原构建工件 |
| GET `/api/ontology-assets/<id>/versions/<v>/artifacts/<artifact-id>` | 当前版本的登记字节下载与完整性保护 |
| `ui/modules/robotics-review.js` 与必要样式 | 原生 JS 控制器；知识表单、依据、差异、冲突、评审/发布与历史 |

### HTTP 字段与响应边界

所有 JSON 写入只接受对象，拒绝多余字段、缺失字段和错误类型；字段中 `actor`、`reason`、`request_id` 必须为非空字符串。
`actor` 是用户声明的操作者，不把它描述为已认证身份；本轮不增加账户体系。
`expected_revision` 必须为非负整数，布尔值和数字字符串不接受；摘要必须为 64 位十六进制字符串。
完整 RDF IRI 通过 JSON 字符串传递，不截取片段作对象身份，也不把 IRI 当成路由路径。

| 请求 | 正文 / 查询字段 | 成功响应 |
|---|---|---|
| drafts | `base_version_id, base_payload_sha256, expected_head_version_id, expected_revision, request_id, actor, reason, operations` | 201，新版本记录 |
| evidence | 上述条件写入字段去掉 operations，加 `evidence` | 201，新版本记录 |
| evidence-reviews | 条件写入字段去掉 operations，加 `evidence_iri, decision` | 201，新版本记录 |
| knowledge-reviews | 条件写入字段去掉 operations，加 `entity_iris, decision` | 201，新版本记录 |
| diff | 唯一且必填查询参数 `base, target`；同资产版本 ID | 200，`base, target` 固定引用及 `changes` 语义变化数组 |
| merge-previews | `base, current, incoming, expected_head_version_id, expected_revision` | 200，`merge_token, conflicts, inputs`；只读预览 |
| merges | `merge_token, inputs, decisions, request_id, actor, reason` | 201，新版本记录 |
| evidence-sources | multipart，仅一个 `file` 文件字段；不接受 URL、服务端路径或额外表单字段 | 201，`source_asset_id, role, sha256, byte_size, media_type` 等公开来源信息 |
| versions/<v>/reviews | `payload_sha256, decision, actor, reason, request_id` | 201，固定版本审核记录与结构/缺口/证据报告 |
| versions/<v>/publish | `payload_sha256, review_id, expected_head_version_id, expected_revision, request_id, actor, reason` | 200，已发布版本记录；版本 ID 取自路由 |
| evidence-revocations | `evidence_iri, version_id, kind, actor, reason, request_id` | 201，追加记录；kind 为 revoked / superseded |

`decision` 为 accepted / rejected；审核版本、审核证据和审核业务对象是三个独立动作，不能相互替代。
`operations` 沿用 T02/T03 受控编辑格式和依据字段；不允许提交整个 RDF 替换现有载荷。
`evidence` 固定字段为 `iri, kind, implementation, part_type, slot, reference, source_asset_ids, valid_until`，沿用 T03 范围/期限规则。
合并引用固定为 `ontology_id, version_id, payload_sha256`；decisions 沿用 T03 以冲突 ID 为键的显式选择格式；不得凭任意 OWL 副本推测来源构建。
除附件上传外，有 request_id 的写入保持已有服务的持久幂等语义，重放返回原结果及同一成功状态码；同 ID 改输入返回 409。
附件只是不可变来源材料；上传不生成候选、不确认其内容，也不等价于证据登记或审核。重复上传可生成新的来源 ID。
文件名不参与存储寻址；上传读入受现有 file_bytes 与应用总请求上限共同限制；空文件拒绝，不借用 CSV/数据库上传链路。

knowledge 响应包含 `ontology_id, version_id, payload_sha256, head, knowledge, evidence_policy, evidence, revocations, history, reviews, task_readiness, execution_readiness`。
knowledge 内的 entities/schema/provenance/issues 直接投影本版本 RDF；evidence 列出本版本证据的完整 IRI 与当前有效性/失效原因，revocations 为资产当前撤销台账。
history 为资产追加历史；reviews 仅返回针对当前固定版本的审核记录；旧版本知识不会因工作头移动而切换成新版本内容。
head 包含 `version_id, revision`，只读查询不得初始化工作头。
没有额外任务评估时 task_readiness=not_assessed，execution_readiness 始终 not_verified；结构审核通过不等于现场执行验证。
版本详情对机器人候选增加 `payload_url`；未发布治理候选 artifacts=[]，不能把 origin_build_run_id 的旧 OWL 当成新候选工件。
发布后详情 artifacts 提供当前版本 ontology/validation/manifest 的登记元数据及版本下载 URL；旧构建详情及运行下载 URL 保持可用。
payload 与工件下载均校验资产/版本归属、固定摘要、大小、路径与符号链接；损坏返回错误 JSON，不附带下载头或返回部分文件。
工件字节保持已登记摘要；manifest 可包含登记的相对位置，不泄漏存储绝对路径，也不临时改写已登记清单。

新增路由错误格式为 `{"error":{"code":"...","message":"..."}}`。
400 invalid_request 表示请求形状/类型/空身份字段或错误领域入口；422 domain_validation 表示格式合法但映射、参数、证据或执行结构不符合领域规则。
404 not_found 表示缺少或不属于该资产/版本的记录；409 保留 head_conflict/request_conflict/review_conflict/merge_conflict/integrity_conflict，下载损坏使用 artifact_integrity。
413 file_too_large 表示上传超限；503 feature_disabled/storage_unavailable 分别表示关闭与存储不可用；不得泄漏原始异常或本机路径。
应用级 Host/CSRF/JSON guard 已有响应形状与 400/403 状态保持兼容，不要求它们改成新增路由错误格式。
关闭开关时所有新增 POST（含只读的合并预览 POST）均返回 503；GET 仍允许读取已有兼容存储。
缺库 GET 返回 404 且不建库；schema 4 只读 GET 返回 503 且不迁移，升级仍由显式可写初始化承担。

复用服务端 CSRF/JSON guard、请求限制、DATAMIND_ROBOTICS_ENABLED；关闭时新增写入口不可用，历史与版本仍可读。
返回 400 字段/类型错误、404 缺记录、409 版本/幂等/审核/完整性冲突、413 超限、422 领域校验失败、503 功能关闭/存储不可用；不得靠错误 HTML 伪造成功 JSON。
UI 只对机器人版本显示维护入口，明确基线及未保存变化；切换/离页前处理未保存输入，旧响应不覆盖新版本。
并发冲突保留本地输入并展示当前服务端版本，不能自动重发覆盖；审核/发布双击不产生重复记录。
修改后跳转新候选固定版本，原深链仍指向原版本；成功/失败、待审核/已退回/已发布与适配/执行状态分别显示。
本体评审中的机器人区独立于现有数据评审；现有数据图谱、聊天及作业入口照常可用。

## 任务 Tasks（每项一次提交）

| 编号 | 工作与提交边界 | 前置 | 对应验收 |
|---|---|---|---|
| T01 | 本轮代码盘点、IR、计划验收与审计起点；提交具体范围确认 | IR-018 delivered | 全部 |
| T02 | 编辑/证据/差异合并/治理存储/发布准入/迁移与旧生成兼容的失败测试，记录 RED 后提交覆盖审查 | Goal/Acceptance 确认 | RKG-01～08、09 的存储部分、11 |
| T03 | 领域和治理服务实现，使 T02 转绿；已审测试保持不变，全量回归 | T02 review | 同上 |
| T04 | HTTP/附件/条件写入/审核发布/当前版本下载/开关及实际应用装配失败测试，提交覆盖审查 | T03 | RKG-02～09、11 |
| T05 | 实现治理 Blueprint/装配及版本详情接线，使 T04 转绿，全量回归 | T04 review | 同上 |
| T06 | UI 状态和真实后端浏览器失败测试：保存/冲突/评审/发布/合并/下载/重启，提交覆盖审查 | T05 | RKG-09～12 |
| T07 | 实现知识维护与评审 UI、统一真实浏览器检查；补离线演示并核验，全部已审测试转绿 | T06 review | RKG-09～12 |
| T08 | 汇总全量证据、逐项交付对照、操作指南及剩余边界，提交整轮人审 | T07 | 全部 |

T02/T04/T06 的具体测试数量在实际编写后记录，不在规划阶段虚报“覆盖已通过”。
离线演示 CLI 行为也须在 T06 先写失败测试；实现阶段不得改已审断言来消除失败。

## 验收 Acceptance（范围已确认，待实现与交付验证）

- [ ] **RKG-01 权威语义与编辑边界**：受控操作只改变允许的业务知识，保留 RDF 身份、类型/语言/空白节点/列表与未改语义；保护技能模板、端口、调用、规则和来源事实；不隐式合并同名对象。
- [ ] **RKG-02 不可变与并发**：每次保存生成固定新候选，父版本和来源可追溯；旧版本不变，工作头及输入摘要检查拒绝过期写入；重发幂等且失败不留下半份可见版本。
- [ ] **RKG-03 映射与参数依据**：实际维护零件类型、工位/载具/槽位和参数配置关系及参数；缺值/单位/坐标系可保存并明确报告；错误值和引用被拒绝，源数值不会自动变成适配事实。
- [ ] **RKG-04 证据审核与隔离**：证据有来源、对象/实现/配置范围、时效和审核记录；保存/发布不自动确认；新 real 治理版本拒绝模拟依据，未知信息不靠默认值补齐。
- [ ] **RKG-05 失效与准入**：来源模型/参数/映射变化、过期、撤销或替代使相应证据失效；新候选不继承旧审核；平台严格生成保持 needs_information/错误且无候选，历史报告不被改写。
- [ ] **RKG-06 语义差异与重建合并**：三方基线和新来源身份固定；保留可重放的人工修改，展示冲突，拒绝按旧步骤序号猜对应；未解决冲突或过期预览不能提交，旧版本和原构建不变。
- [ ] **RKG-07 审核与发布**：审核固定版本/摘要/理由，发布复验实际 OWL 和受信规则；拒绝未审核/被退回/过期审核/非法结构，重复请求不重复发布；结构发布不等于全任务适配或执行通过。
- [ ] **RKG-08 存储与旧契约**：schema 4 升级及故障回滚保持原资产、运行、来源、manifest；只读和未知版本遵守既有约束；旧发布/生成/演示可复现，新治理策略不被旧模式绕过。
- [ ] **RKG-09 版本工件与保护**：编辑后下载的是本版本 OWL/报告/清单，摘要与登记一致；不误用原构建结果；错误身份、损坏、越界、功能关闭和超限明确拒绝，旧下载入口保持兼容。
- [ ] **RKG-10 UI 操作与冲突呈现**：用户可在现有入口完成知识保存、证据查看/审核、差异、冲突处理和发布；未保存输入不被迟到响应或 409 覆盖；键盘、1440/768/390 布局和文字安全通过。
- [ ] **RKG-11 兼容及真实负向**：原数据构建/图谱/评审/聊天与 IR-016～018 无回归；真实缺证据可发布结构合格版本但严格生成仍不成功；execution_readiness 始终未验证，模拟成功不冒充现场适用。
- [ ] **RKG-12 可运行交付与联动**：实际浏览器走通新来源候选→维护→比较→审核发布→当前版本下载→刷新/重启回查，并覆盖重建冲突和失效；离线演示直接可运行，保存版本/审核/来源/下载证据，全量检查通过。

### 可直接运行的离线演示交付契约

计划新增 `scripts/demo_robotics_review.py`，使用显式新目录及冻结 XML，不依赖 UI 或外部模型；入口在 T07 实现，当前不可执行。

```bash
.venv/bin/python scripts/demo_robotics_review.py --case all --output-dir /tmp/datamind-review-demo
.venv/bin/python scripts/demo_robotics_review.py --verify /tmp/datamind-review-demo
```

若目标目录非空，拒绝覆盖，要求使用新目录；verify 只读，不修复或重建被篡改产物。
演示包含：保存不完整候选并显式发布/严格生成阻断；两编辑者并发冲突；新来源与人工修改冲突处理及证据重新评审提示；文件篡改被核验拒绝。
自动化审核资料为明确标注的合成测试输入，只验证审核/准入契约，不证明机器人现场能力；真实模式默认缺证据的负向示例必须保留。
输出 summary、固定版本、父/来源/合并引用、审核记录、问题报告与摘要；独立核验从存储重新读取、比较旧字节和当前工件，不只相信 summary 自报。
浏览器程序计划 `tests/frontend/robotics-review-browser.py`，必须进入 scripts/check.py --suite all/ui，连接实际隔离服务，不拦截核心 API 返回固定成功。
全量沿用既有检查段、81% 覆盖率门槛和有界预算；实际新增耗时如需预算调整须记录依据，不削减已审测试。

## T01 记录

本次仅编写规约、计划映射和审计，不改生产实现、已审测试、.env 或业务存储。
planned_requirements 中列预计测试文件，尚无真实测试函数引用；只有实际写好测试后才迁入 requirements。
用户确认本轮 Goal/Acceptance 后进入 T02，写 RED 测试再提交覆盖审查；新阶段的产品取舍不以 IR-018 交付确认代替。

## T02 测试接口与审查范围（2026-11-05）

以下为本批 RED 测试约定的 Python 服务边界，尚未实现；HTTP 参数在 T04 再固定。
本批覆盖领域/存储以及版本工件的存储部分，不能据此宣称 HTTP、UI 或整轮验收完成。

| 模块 / 接口 | 本批固定的行为 |
|---|---|
| `domains.robotics.authoring.apply_operations(graph, operations)` | 返回新 Graph；`create_entity / set_field / set_relation / delete_entity` 携完整 IRI、类型化字段和 basis；输入图不变，失败不部分修改 |
| `authoring.inspect_knowledge(graph)` | 从 RDF 生成 entities、provenance、issues、execution_readiness；同名保留身份，未知值明确报告 |
| `domains.robotics.evidence.register_evidence(graph, evidence, mode, now)` | 返回待审证据图；来源主 XML、节点模型及配置摘要由当前图计算，不能接收伪造摘要/confirmed 字段 |
| `evidence.review_evidence / evaluate_evidence` | 精确内容署名审核；按 now、撤销记录和 required_scope 判断有效性，保留具体失效原因 |
| `domains.robotics.version_diff.diff_graphs / merge_graphs` | RDF 语义比较；三方输入不变；未解决时 graph=None、conflicts 非空；字段冲突、删除冲突、身份变化和证据范围分别处理 |
| `ontology_assets.governance.GovernanceService(root, read_only=False)` | 复用资产存储事务，初始化新 schema；read_only 不创建或升级存储 |
| `head / save / history` | 头包含 version_id/revision；首次虚拟 revision=0，读取不落治理记录；保存含基线、摘要、当前头、修订、请求 ID、署名、理由和操作；返回固定版本 |
| `register_evidence / review_evidence / review_knowledge` | 共用条件写入字段，每次创建新候选；登记与确认分开；知识审核只确认明确选择的对象，不替代实现适配证据或实机验证 |
| `review / publish` | 审核与发布分开；review 固定 version_id/payload_sha256 和报告摘要；publish 再检查头、review_id、当前字节、可信规则及工件事务 |
| `preview_merge / commit_merge` | B/L/R 均携资产、版本、摘要；预览只读；提交固定 token、原输入、决定和当前头，重复请求返回原版本 |
| `artifacts / read_artifact` | 当前版本独立的 ontology/validation/manifest；关联版本/载荷摘要，读时核验字节，禁止借用原构建工件 |
| `revoke_evidence` | 按资产/版本/证据 IRI 追加撤销，幂等；平台生成读取最新撤销，已发布 OWL 和历史运行工件不改 |

`GovernanceError` 是带 code 的 ValueError；本批固定 head_conflict、request_conflict、merge_conflict、integrity_conflict 等服务冲突含义，HTTP 映射后续验收。
模式从固定基线与来源确定，普通编辑不能把 real 改为 demo，也不能去掉新证据策略声明。
治理图使用服务端控制的 `evidencePolicy=robotics-governance-v1`；证据的 modelsHash/configurationHash/reviewedContentHash 等字段与旧 sourceHash 共同限定有效性，不用第二份可编辑 JSON 代替 RDF。
证据审核记录绑定具体内容，普通参数编辑不能直接设置 confirmationStatus；知识对象确认另行明确选对象、署名和理由，并保存为新候选。
生产时间由服务端提供；纯领域测试显式注入带时区的 now，测试到期边界不依赖机器时钟。

参数维护沿用 threshold、exposureTime、modelName、objectName、rawValue、lengthUnit、angleUnit、expressedInFrame 等实际词表。
缺失单位/坐标系允许作为草稿保存并报告；填写错误类型、NaN、错误维数、悬空引用或无依据操作拒绝。
字段冲突决定为 current/incoming/manual；manual 仍须合法；证据范围变更只允许明确作废并重新审核，不能通过保留旧字段复活。
调用索引变化属于来源身份冲突，不能以选择 current 绕过受保护模板结构。

冻结夹具 `tests/fixtures/domain_storage/schema4_snapshot.json` 由真实 schema 4 代码生成，记录生成提交，复用 schema2 冻结文件的摘要和原字节。
恢复测试直接建冻结 SQL/文件，不调用当前初始化器伪造旧 schema；包含旧状态运行、已完成新来源构建、排队构建和去重记录。
所有材料、署名、审核及 demo 正例均为合成测试输入；真实负例仍要求缺证据不能生成。
本批结果、实际映射、失败原因和覆盖审查清单见 [T02 审计](../../docs/audits/IR-019-knowledge-validation.md)。


## T03 实施记录（2026-11-05）

用户在了解 T02 的五组测试后回复“继续下一步”，视为确认覆盖并授权实现；全部 127 个冻结测试/夹具文件保持原样。
领域实现为 `authoring.py`、`evidence.py`、`version_diff.py`，治理事务服务为 `ontology_assets/governance.py`。
新增 schema 5 以追加表保存工作头、事件、幂等请求、审核、版本工件和证据撤销；保留原资产/版本/运行表与载荷。
保存、证据登记/审核、知识对象确认和合并均产生新候选；版本审核与发布保持独立，发布重验固定字节及受信消费契约。
服务历史包含操作对象/决定、父版本、载荷摘要、署名、理由、修订号；署名仍是人工声明，不是登录身份认证。
新治理策略由生成内核消费，平台补充读取撤销记录；旧 IR-016/017 载荷仍按原策略消费。
重建需真实成功构建的基线与新来源，核对运行来源快照及原构建 OWL 工件；后续再次重建可追溯此前显式合并的跨资产来源基线。
任何发生冲突的步骤身份不猜测对应；证据范围变化只能明确作废，不能通过保留人工修改恢复有效。
本轮没有新增 HTTP 路由、知识维护 UI 或演示 CLI；这些分别由 T04/T05、T06/T07 的已批准任务承接。
首轮定向实测：122 通过，0 失败/错误/跳过；完整回归与最终摘要记录在审计文件。


T03 服务调用、存储迁移边界和后续入口说明见 [本地服务指南](../../docs/robotics-knowledge-service.md)。
平台生成在提交结果的同一事务内再次检查证据与撤销记录，避免计算期间的撤销被遗漏；新结果保存检查时间、策略与撤销 ID，旧运行契约和历史结果保持兼容。


T03 最终结果：原有 127 个测试/夹具文件摘要不变；全量十段退出码均为 0，单元 1320 通过/5 可选上游跳过，HTTP 551 通过/8 条件跳过，严格场景 10/10。
旧 UI 64 通过，UI 实操 50 通过/3 未配置模型跳过，原构建浏览器 10 项、机器人构建真实浏览器 14 项通过。
最终代码覆盖率 85.3%（门槛 81%）；全量初次覆盖率 84.7%，最终修改的两文件先清除旧行号数据，再以 47 项已审测试及提交竞态实验重新采集。

单元检查实测 1196.827 秒，距原 1200 秒上限不足 4 秒；因此 scripts/check.py 将单元段有界预算增至 1500 秒，其余预算、测试选择和覆盖率门槛不变。
该全量运行已在更严格的原预算内通过；预算调整本身已通过静态与 CLI 接线检查，不另重跑相同的全量测试。
T03 为后端切片完成，完整 UI/HTTP/CLI 的交付和 RKG 最终勾选仍由后续 T04～T08 承接，不把本次后端 GREEN 记为整轮 delivered。


### T04 执行记录（2026-11-05）

HTTP 契约与失败测试已编写；34 个函数展开为 83 项，最终 7 项对照通过、76 项预期失败，0 错误、0 跳过。
现有全局保护 6 项与 T03 夹具对照 1 项已通过，不代表新增 HTTP 已实现；失败来自新增路由缺失或版本详情尚无新工件字段。
元测试 21 项、ruff、mypy 和 diff 检查通过；原 127 个已审测试/夹具与 T03 生产实现保持不变。
覆盖矩阵、运行命令、失败边界见 [T04 审计](../../docs/audits/IR-019-knowledge-validation.md#t04--http-red-覆盖审查2026-11-05)。
当前等待 SDD 第⑤步的覆盖审查；T05 HTTP 实现与全量回归尚未开始，Deliverables/Acceptance 不提前勾选。


### T05 覆盖授权与实现（2026-11-05）

用户明确回复“这部分测试覆盖确认没有问题，继续下一步实现。”，T04 覆盖审查通过。
本任务保持已审测试不变，接入独立治理 Blueprint、版本详情与下载；按 SDD 第⑥～⑦步运行定向和全量回归。


T05 最终结果：新增治理 HTTP、固定版本知识/差异/附件读取与写入、审核发布及版本下载已实现；未修改已审测试。
定向 158 项通过；全量独立重跑十段均退出 0，其中单元 1403 通过 / 5 可选跳过，覆盖率 85.4%，HTTP 551 通过 / 8 条件跳过，严格场景 10/10，UI 64 通过，UI 实操 50 通过 / 3 未配置模型跳过，原构建浏览器 10 通过，机器人真实浏览器 14 通过。
首轮外层 SIGTERM 中断未计为完整通过；保留日志并独立重跑同一全量命令取得退出码 0，未缩减检查或修改预算。
详细结果与中断记录见 [T05 审计](../../docs/audits/IR-019-knowledge-validation.md#t05--治理-http-与版本下载实现2026-11-05) 和 [机器审计](../../docs/audits/IR-019-t05-green-results.json)。
本批仍不包含新知识维护 UI / 独立演示 CLI；下一步 T06 先写状态、浏览器与 CLI 失败测试并提交覆盖审查，整轮 Deliverables/Acceptance 不提前勾选。
