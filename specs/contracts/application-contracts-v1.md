# 应用契约基础 v1 — DR-071 / IR-025 T01

- 状态：T01/T04 调用面已获人审；T05 公共核心及集成已实现，IR-025 已于 2026-11-12 用户确认交付。
- 日期：2026-11-12。
- 依据：[DR-071](../decisions/DR-071-application-contract-registry.md)、[IR-025](../iterations/IR-025-application-contract-registry.md)及用户确认的“构建完成直接入库，点击应用按钮才检查”流程。
- 本文件保留冻结约定与测试设计历史；已实现的 API、最小默认适配和后续领域边界见 [使用说明](../../docs/application-contracts-guide.md)。

## 1. 本轮范围与既有行为

构建保持现有 XML 输入、LLM／离线路由、TBox 确认和 ABox 实例化流程。
构建完成直接入库，不调用应用契约服务，不增加应用选择页面。
目录、详情、图谱和下载只读取本体及已有检查记录，不自动评估或修复。
用户点击“基于此本体生成行为树”后，页面发起显式检查请求，后端绑定 `robotics.pick_place`。
按钮可以进入诊断，不代表已经允许生成；缺项不能成为隐藏该按钮的理由。
缺项补全后返回原任务，用新固定版本重新检查，再决定能否生成。

现有代码有三项需要迁移：

| 位置 | 当前行为 | 新约束与责任 |
|---|---|---|
| `bp_robotics.py` 的 catalog/version 与 `bp_robotics_review.py` 的 visualization | 读取时计算 `DesignAssets.compatibility` | IR-025 移除浏览链路即时应用检查，保留本体完整性校验；读取历史摘要 |
| `ui/modules/robotics-assets.js` 的详情动作 | unsupported/invalid 时隐藏行为树按钮 | IR-025 完成最小按钮及检查入口；IR-029 改善完整视觉与导航 |
| `TaskService.start`、生成器及消费者 | 开始任务前要求旧执行结构；生成时仍有校验 | IR-025 在前面增加诊断入口，保持原守卫；IR-026 接修复和更完整适配 |

IR-025 交付公共注册、检查协议、持久化、最小机器人入口和执行前复核接点。
机器人初始适配器只复用现有消费者能确定的检查，不能将笼统失败伪装成精确的缺类诊断。
精细 TBox/ABox 缺项、提取扩展及修复处理器由 IR-026 实现。
深度问数完整适配由 IR-027 实现；IR-025 预留注册描述，未接通时明确 unavailable，不改变既有问数链路。
删除生命周期由 IR-028 提供；IR-025 定义读取接点，不提前声称已实现删除。

## 2. 注册表与后端边界

新增 `application_contracts/` 包，职责分为 registry、models、service、store 和 adapters；具体文件拆分可在 GREEN 阶段调整。
注册项包含 `application_id`、`contract_version`、`entrypoints`、`accepted_payload_kinds`、`checkers`、`repair_handlers`、`executor_id`、`availability`。
初始 ID 为 `robotics.pick_place` 和 `data.deep_qa`，契约版本使用字符串 `1`。
执行器、检查器和修复器由代码白名单注册，不从 HTTP、模型输出或配置文本动态导入代码。
重复 ID、重复入口或缺少必需检查器导致注册失败；不得覆盖已有项或默认通过。
检查结果必须覆盖注册项声明的全部必需 check_code，且不得重复；空结果、漏项或未知状态均为 error，不能以空集合全通过。
同一版本允许被不同应用检查，分别保存结论。

`robotics.generate_behavior_tree` 入口固定映射到 `robotics.pick_place`。
`data.ask` 入口固定映射到 `data.deep_qa`，在 IR-027 接入现有问数请求；不新增应用选择向导。
客户端提供入口枚举和版本引用，不提供可信检查结果、执行器、修复器路径或服务端配置。
具体生成路由必须校验其固定应用绑定，其他应用的报告不能用于行为树生成。

内部边界建议为：

- `registry.resolve(entrypoint)`：返回固定注册项，不触发模型或资产扫描。
- `snapshot_resolver.resolve(asset_refs)`：读取精确版本／有效数据 IR，返回独立快照及摘要。
- `check_service.evaluate(entrypoint, asset_refs, context, request_id)`：执行确定性检查，保存报告，不修改本体。
- `check_service.read(check_id)`：读取历史报告，不重新运行检查器。
- `execution_guard.require_ready(entrypoint, trusted_context)`：从服务端取得当前快照、任务和环境后复核；不接受客户端 ready。
- `repair_dispatcher.submit(check_id, issue_id, action_id, input, expected_revision, request_id)`：仅调用已注册且与该缺项匹配的动作。

检查器本身不调用 LLM，不读取未经授权的任意路径，不产生新本体版本。
检查记录写入与本体修改是两回事：显式 POST 检查可以保存报告，检查过程对本体及来源只读。

## 3. 输入、快照与模式

机器人输入使用 `ontology_id` 和 `version_id`；禁止把 `latest` 当作固定版本。
每个适配器声明允许的引用数量；机器人第一版恰好一份固定版本，多图仅用于相应数据适配器，不接受空数组或重复引用。
摘要、领域、payload_kind、父版本和生命周期均由后端解析；客户端传入这些断言不能替代读取。
资产存在但不属于请求版本返回 not_found；跨领域载荷返回不支持该应用的报告，不调用执行器。
数据旧 graph key 在 IR-027 解析为包含 edits 的有效快照，多图保存完整有序引用向量。
摘要基于实际读取的有效内容，而非文件名、页面名称或单个图的版本号。

模式保留现有两个维度，不混用：

| 字段 | 当前语义 | 检查规则 |
|---|---|---|
| `mode` | `real` / `demo`，资产及任务模式 | 继续调用现有模式验证；禁止真实任务借用模拟证据 |
| `generation_policy` | `offline` / `strict`，生成证据策略 | 由服务器配置／已有会话确定；浏览器不能用此字段降低要求 |

本轮不把旧任务 API 的 mode 改名为 offline/strict，也不改变现有默认策略。
无任务时 `task_ref=null`；有任务时读取服务端会话 ID、revision 和标准化任务内容。
任意客户端 task_hash、environment_revision、contract_version 和 ready 字段均不被信任。
环境快照记录与该应用有关的配置、连接／证据修订，不包含密钥或密钥摘要。

## 4. 报告和状态

报告包含 `schema_version`、`check_id`、`entrypoint`、`application_id`、`contract_version`、`asset_refs`、`payload_hashes`、`task_ref`、`task_hash`、`context`、`environment_revision`、`checked_at`、`layers`、`issues`、`allowed_actions`。
`layers` 固定包含 schema、execution、task、environment，各层含 checks 数组及汇总。
每个 check 包含 `check_code`、`required`、`status`、`issue_ids`；可选检查的 warning 不代替必需检查。

| 单项状态 | 含义 |
|---|---|
| passed | 所检查事实满足当前要求 |
| missing | 信息缺失，尚不能判断满足 |
| unsupported | 超出已支持范围或适配器能力 |
| invalid | 已有内容冲突、不合法或完整性失败 |
| error | 检查器异常或依赖读取失败 |
| not_evaluated | 未提供任务或前置检查未完成；必须包含原因 |

不适用项明确记录 `applicable=false` 及原因，不用 missing 表达“不需要”。
适用性由契约检查器决定，不能由客户端声明。
必需且适用的项只有 passed 才满足；missing/unsupported/invalid/error/not_evaluated 都不能授予执行资格。
汇总先保留所有单项，不因首个缺项丢失其他可独立检查的事实；依赖不满足的下游标 not_evaluated。
有检查器异常时不得将 error 转为 missing 或忽略。

对外摘要分三个字段，不混为一个绿色状态：

- `preparation_status`：available / blocked；schema、execution 和资产完整性等任务准备前置条件通过后才能 available。
- `execution_status`：ready / blocked / not_evaluated；未有任务时只能 not_evaluated，有任务时所有必需项通过才 ready。
- `freshness`：current / stale / unknown；这是读取视图，历史报告的原始判断保持不变。

未提供任务时可以显示“可准备任务”，不能显示“已可生成”。
无历史记录显示“尚未检查”，不是 missing，也不应触发检查。
本次检查通过不表示机器人现场已验证；`execution_readiness` 继续使用既有独立状态。

## 5. 缺项与修复协议

issue 必须有 `issue_id`、`code`、`layer`、`severity`、`message`、`expected`、`observed`、`entity_refs`、`evidence_refs`、`repair_actions`。
issue_id 在该固定报告内唯一；报告跨版本不保证相同 issue_id，稳定分类依赖 code。
证据只能指向已解析的版本、实体、源节点或平台引用，不泄露文件系统绝对路径、模型密钥或原始端点。

| 问题代码示例 | 引导方向 | 实现归属 |
|---|---|---|
| schema.missing_role | 基于父版本修订类别／关系／属性 | IR-026 |
| execution.binding_ambiguous | 查看源节点，重新提取／适配执行知识 | IR-026 |
| execution.consumer_incompatible | 展示已有消费者诊断；不猜测具体缺类 | IR-025 初始适配 |
| task.missing_profile | 当前任务的业务对象／配置补全 | 复用任务补全，IR-026 接通 |
| environment.table_unavailable | 数据连接或表绑定修复 | IR-027 |
| system.checker_failed | 重试检查，保留可定位错误代码 | IR-025 |

repair action 包含固定 `action_id`、label、kind、availability 和目标上下文。
目标上下文包含 expected_revision 对应的资源种类、资源 ID 和修订；禁止将报告号、本体版本号与任务修订号互换。
kind 区分 navigate（打开编辑／补全页面）与 submit（明确触发修复运行）。
availability 为 available / unavailable；未实现的处理器必须 unavailable，不生成死链接，不暗示自动修复已可用。
GET 不执行修复；检查也不隐式开始模型调用或重跑提取。
已存在修复动作仍需用户在对应界面确认具体修改；修复完成后以新版本复检并返回原任务。
业务数据修改产生派生版本；只修改连接或运行环境不会凭空产生本体版本。

## 6. HTTP 边界

所有新入口复用平台 Host、Origin、JSON 及体积校验，不另建认证体系。
字段白名单严格校验；revision 为非负整数且不接受 bool，未知字段、空引用或未知入口返回 400。
ID 长度和请求体上限复用既有 128 字符 ID 及宿主限制；请求不得携带本体字节、任意文件路径或密钥。
公共错误沿用 `{"error":{"code":"...","message":"..."}}`。

| 请求 | 行为与响应 |
|---|---|
| `GET /api/applications` | 200，静态注册描述和 availability；不检查本体、不调用模型、不建库 |
| `POST /api/application-checks` | 输入 entrypoint、asset_refs、context、request_id；201 新报告／200 幂等重放 |
| `GET /api/application-checks/<id>` | 200 历史报告和 freshness 视图；未知报告 404；不建库、不迁移、不重新检查 |
| `POST /api/application-checks/<id>/repairs` | 输入 issue_id、action_id、input、expected_revision、request_id；200 导航结果／202 已受理运行；具体领域动作后续接通 |

机器人检查 context 可以包含既有 mode 以及可选 task_ref，generation_policy 由服务端补齐。
同一 request_id 和相同标准化输入重放原报告；相同 ID 不同输入为 409/request_conflict。
刷新检查必须使用新 request_id；重放的旧报告不会因为请求重发而重新变为有效。
合法请求得出缺项／不支持／内容不合法，仍保存诊断报告并返回 201；请求格式错误和资产不存在不伪装成诊断成功。
检查器异常若能保存报告，报告含 error 且执行阻塞；存储失败返回 503/storage_unavailable，不返回虚假 check_id。
功能未启用或适配器尚未接通返回 503/application_unavailable；未知入口返回 400/unknown_entrypoint。
修复不存在返回 400/unknown_action，已知但未实现返回 409/repair_unavailable，版本或报告已过期返回 409/stale_result。
直接调用既有生成接口仍保持当前错误结构与状态码，附加结构化缺项不删除旧字段。

## 7. 持久化、新鲜性和执行复核

复用现有 `domain.sqlite3` 存储基础，增量迁移检查报告及幂等请求记录；只在允许写入的初始化阶段迁移。
报告与幂等结果同事务提交；检查失败不创建资产、版本、运行或源文件。
读取检查报告不能触发域库创建、迁移、模型恢复或任务受理。
对长检查使用已固定的输入快照，保存前复核相关修订；变化时拒绝以当前结果授权，并要求重新检查。
历史记录保留输入摘要和当时结论；latest 状态不覆盖旧报告。

新鲜性覆盖：契约版本、全部资产和有效编辑摘要、任务 revision／内容、mode、generation_policy、环境与生命周期修订。
只读 GET 可以比较已有本地修订；若无法在不运行检查器或网络探测的情况下确认，返回 unknown，不运行现场检查。
真正执行前重新读取上述输入，执行必需检查并调用既有消费者／编译／SQL 守卫。
check_id 是追溯引用，不是权限令牌；客户端提交旧报告或其他应用通过报告均不能绕过复核。
通过后受理执行必须绑定所检查的固定快照；任务／配置修订变化不得悄悄改用新输入。
IR-028 再接入删除与受理的同事务生命周期保护；IR-027 接入连接变化与问数缓存保护。

## 8. 兼容与范围交接

`application_compatibility` 历史字段可保留为报告投影，但不能再在目录、详情、图谱 GET 中即时调用消费者。
无历史应用报告时返回 not_assessed；旧字段不作为新执行授权依据。
显式 `/compatibility` GET 改为只读已有结论并标明是否过期；主动检查统一使用 POST。
这会改变 IR-024 中“目录返回即时资格、隐藏不兼容按钮”的旧要求，是用户新方向下有意替代的行为。
T04 RED 阶段需评审并更新相关旧测试断言；不得在 T05 GREEN 阶段偷偷改测试。
旧工件、历史下载、本体构建、真实／演示模式和 offline／strict 生成边界保持原约束。
未实现领域适配时，不将原先无法生成的设计候选标为 ready。

## 9. 测试设计与验收映射

| 验收 | T02 核心 RED | T04 集成 RED |
|---|---|---|
| ACR-01 | 不完整但合法资产不受应用服务影响；检查不改写来源／本体 | 原双路径构建直接入库；目录、详情、图谱零检查调用 |
| ACR-02 | 同版本多应用、注册冲突、任务为空、两种模式维度 | 按钮触发一次显式检查，无额外选择；未检查／有缺项入口可用 |
| ACR-03 | 状态聚合、精确问题协议、未实现动作、完整摘要向量失效 | 修复上下文、幂等、版本冲突、重启历史读取；真实领域修复另由 IR-026/027 验收 |
| ACR-04 | 未知入口、错误载荷、异常／不完整检查器、伪造状态 | 直接生成 API、跨应用报告、旧报告、服务关闭及存储失败均不能绕过 |

建议 T02 测试文件为 `tests/unit/test_application_contract_registry.py`、`test_application_contract_checks.py`、`test_application_contract_freshness.py`。
T04 再添加 HTTP／存储／浏览器场景并迁移冲突的旧展示断言。
公共核心测试可以注入合成检查器，但不能以合成通过冒充机器人或问数适配已完成。
T02 必须可正常收集，因缺少目标能力产生预期失败；同时保留原构建的正向基线。
T02 已创建核心测试，ACR-01～04 已进入 requirements 并标为 created-red-partial；HTTP／UI／持久化覆盖仍待 T04。
T02 执行定向 RED 与构建基线，不运行整套产品回归、不标记验收通过；详细结果见 [T02 评审记录](../../docs/audits/IR-025-t02-red-review.md)。

## 10. T02 核心测试调用面（2026-11-12）

本节固定测试调用的包级门面，内部模块拆分仍可调整。
公共服务持久化报告前复用这些核心函数；纯 evaluate 本身不建库、不创建 check_id，也不承担 HTTP 参数解析。
T04 再测试 CheckService 的报告记录和 HTTP 层，不以纯函数测试代替持久化验收。

- `Registry()`、`register(spec)`、`resolve(entrypoint)`：管理后端登记项；异常为 `ContractError`，有稳定 `code`。
- `evaluate(*, registry, entrypoint, snapshots, task, context, environment)`：返回分层报告内容及 basis，使用已由服务端解析的输入，不接受客户端检查结果。
- `make_basis(*, application_id, contract_version, snapshots, task, context, environment)`：返回独立可序列化的判定依据，按实际 payload 字节计算摘要。
- `compare_freshness(report, current_basis)`：纯比较，返回 current/stale/unknown；current_basis=None 表示不能通过只读本地信息确认。
- `require_ready(*, registry, entrypoint, resolve_context, previous_report=None)`：重新调用可信上下文读取器和检查器，通过后返回当前报告；未通过抛 application_not_ready。

snapshot 测试结构为 ontology_id、version_id、domain、payload_kind、payload（bytes）、lifecycle_revision；额外传入 payload_sha256 不能覆盖实际字节摘要。
task 为 None 或包含 session_id、revision、content 的可信任务快照。
context 分别保留 mode 和 generation_policy；此处 generation_policy 已来自服务器，HTTP 层不得允许客户端覆盖。
environment 是适配器取得的必要修订信息；真实连接、有效 edits 与删除服务的解析仍由后续适配与集成测试覆盖。

检查器登记项包含 layer、required_codes、required、depends_on 和 check callable。
check 接受独立输入快照，返回检查结果列表；上下文及返回值均不得通过别名污染调用者、独立检查器或后续报告。
检查结果包含 check_code、status、applicable、issues，以及 not_evaluated／不适用时的 reason。
required 由注册项决定，结果内额外的 required=false 不能降低要求。
注册表保留 callable 身份用于执行，但复制可变描述，外部修改 spec 或 resolve 返回值不能重新绑定入口。

报告的 layers 使用四层键映射，每层 checks 列出结果和 issue_ids。
报告包含 preparation_status、execution_status、execution_readiness=not_verified、asset_refs、payload_hashes、task_ref、task_hash、context、issues、allowed_actions 和 basis。
allowed_actions 为可用动作 ID 列表；未注册的建议动作显示 unavailable，不能进入此列表或生成 href。
多个检查器产生相同局部 issue_id 时，报告分配唯一 ID 并保持 check→issue 对应，不能覆盖诊断。

核心错误码使用 registration_conflict、invalid_contract、unknown_entrypoint、application_unavailable、invalid_request 和 application_not_ready。
T02 测试中的多应用、多图和已通过判定均来自明确标注的合成检查器，不能据此宣称真实机器人适配或深度问数已实现。

## 11. T04 集成测试调用面（2026-11-12，已人审并在 T05 实现）

本节将第 2／6／7 节落实为可执行测试调用面，产品行为和原 ACR criterion 不变。
新增测试不提供假实现；目标模块在测试函数内导入，缺少实现应为用例失败而非收集错误。

### 服务与 HTTP

- `application_contracts.service.CheckService(root, registry, resolver, read_only=False)`，后三项使用关键字参数。
- `evaluate(entrypoint, asset_refs, context, request_id)` 全部使用关键字参数，返回 `{report, freshness, replayed}`；report 为不可变历史内容，freshness 为当前读取视图。
- `read(check_id)` 返回 `{report, freshness}`；只读模式下不存在数据库也不能新建，报 not_found；不支持的旧 schema 报 storage_unavailable。
- `repair(check_id, issue_id, action_id, input, expected_revision, request_id)` 全部使用关键字参数；返回 kind、target、return_context、replayed 及 navigate 的 href／submit 的 run_id。
- `require_ready(entrypoint, asset_refs, context, previous_report=None)` 全部使用关键字参数；读取服务端当前事实，调用 T03 核心守卫，返回本次通过的报告内容，不把已有报告当执行许可。
- `bp_application_contracts.init_app(app, root=..., registry=None, resolver=None, enabled=True)` 安装真实 Blueprint；省略依赖时使用后端内置注册／解析器，不允许 HTTP 指定 Python 对象。
- 真实平台启动须接入同一 Blueprint；仅直接挂载测试 Blueprint 通过不算宿主接入完成。
- `GET /api/applications` 返回 `{items:[...]}`，公开 ID、契约版本、入口与 availability，不序列化可调用检查器或修复器。
- POST 检查和 GET 报告分别使用上述服务响应结构；POST 首次 201、重放 200，修复 navigate 200、submit 202，重放保持动作原状态码。
- `context.task_ref` 请求只接受 `{session_id}`；任务 revision/content 均由后端读取，不能由客户端提供。
- 原 Host／Origin／非对象 JSON 前置守卫保持原有错误格式；Blueprint 自己的错误使用第 6 节的结构化 error。

### 可信解析和持久化

解析器 `resolve(entrypoint=..., asset_refs=..., context=...)` 返回第 10 节的核心输入结构。
`peek(report)` 只能读取本地事实，返回同样的结构或 None；不运行检查器、不访问网络、不迁移。
`resource_revision(target)` 读取修复目标资源修订；目标 ID／kind 来自已保存 issue，不能被请求覆盖。
服务负责再次校验注册、输入、完整版本向量和保存前修订；解析器只提供事实，不实现状态聚合或授权。
测试解析器仅将任务／环境设为合成事实，版本和 payload 仍从真实 AssetStore 读取；真实宿主测试使用默认解析器。

增量迁移使用 `application_checks`、`application_check_requests`、`application_repair_requests` 三张表。
测试通过 SQLite 独立读数和故障触发器观察原子性，不替换数据库为字典。
报告与请求记录在同一事务内提交；请求记录插入失败也不能留下孤立报告。
测试覆盖旧 schema 5 冻结快照升级、既有行／工件不变、独立进程只读恢复；不声称已测试所有损坏数据库形态。

本轮公共修复处理器的调用面为 `handler(command, conn)`，conn 为分发器的同一 SQLite 事务。
command 包含固定 issue/action/target、input、request_id，以及 return_context 中原 asset_refs／task_ref。
仅验证本地事务内的受理／导航记录；submit 处理器应登记持久运行而非在该事务内调用模型。
并发重复请求只产生一次本地事务效果；失败回滚，允许相同 request_id 重试。
这不承诺任意外部系统的 exactly-once，也不实现 IR-026/027 的具体修复操作。

### 最小 UI 与旧行为迁移

沿用现有本体详情和图谱的行为树链接，不因 not_assessed 或历史 blocked 隐藏入口。
点击固定版本入口后仅发起一次显式 POST，固定绑定 robotics.generate_behavior_tree，无“选择应用”。
结果容器用 `data-testid="application-check"` 和 `data-check-id` 供浏览器验收定位。
可准备任务和已可生成分开显示；缺项展示未满足原因，未实现修复不能产生可点击死链接。
页面保留 check_id 的可恢复路由状态；刷新和服务重启后读取同一报告，不重发 POST、不调用模型。
旧 compatibility 响应保留 status（无历史为 not_assessed），有历史时附 check_id／freshness；不是执行授权。
T04 更新 3 个旧 HTTP 用例与 1 个旧浏览器用例中冲突的展示断言；原文件中的下载、完整性、拒绝不兼容生成等断言保留。
TaskService 生成链路通过上述服务 require_ready 接点复核，保留原消费者／编译器验证和原任务错误结构。
新公共检查路由不会改造现有任务 API 的请求字段；向 generate 请求塞入 ready、check_id 或其他 application_id 仍为非法字段。
完整视觉优化、真实机器人修复和问数接入仍由后续 IR 验收。
