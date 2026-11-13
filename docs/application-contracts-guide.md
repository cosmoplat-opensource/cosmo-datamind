# 应用契约与机器人适配使用说明 — DR-071 / DR-072

适用版本：IR-025 公共检查及 IR-026 机器人适配已实现；IR-026 T06 技术验收通过，2026-11-13 用户要求提交代码，已确认交付。
本说明描述公共应用检查、机器人角色适配及任务补全；深度问数接入继续由 IR-027 完成。

## 页面使用

1. 在本体构建页面上传 XML，沿用原 LLM／离线路由。
   模型路径确认类别、关系和属性后完成 ABox；构建成功直接进入本体库。
2. 从本体库打开固定版本详情或关系图。
   浏览本身不检查应用；没有历史报告时，应用摘要为“尚未检查”。
3. 点击“生成行为树”或图谱中的“基于此本体生成行为树”。
   系统固定检查 robotics.pick_place，无需选择应用；检查不调用 LLM，也不修改本体。
4. 若显示“可准备任务”，点击“继续描述任务”，进入原任务流程。
   自然语言任务解析仍沿用原模型配置；本体可离线构建不代表自然语言任务解析已改为无模型方案。
5. 若显示“未满足应用要求”，读取报告中的原因。
   缺角色对应时先核对已有概念；确实缺少类别、关系或属性时，点击“修订本体骨架”，按提示进入对应建模阶段。
   缺执行结构时，点击“补齐执行结构”；规范角色自动匹配通过后直接生成应用版本，无法确定时才需人工补选并一次确认生成。
   执行结构完整而任务缺业务信息时，沿用原任务中的零件／工位／载具／槽位选择、配置关联及确认流程。
6. 真正生成前，后端重新读取本体、任务、策略和相关撤销记录并检查，再运行原消费者／编译器。
   生成得到离线候选，执行验证状态仍独立为未验证。

检查页保留 check_id；刷新或重启后读取同一份历史报告，不重新 POST 或调用模型。
需要主动复查时可返回固定版本详情／图谱，再点击应用入口；新检查使用新的 request_id。
检查报告独立保存在域数据库中；原构建的完整 JSON、OWL 及下载方式保持原有工件语义。

## 读懂结果

| 字段 | 结果 | 含义 |
|---|---|---|
| preparation_status | available / blocked | 是否满足进入任务准备的前置条件 |
| execution_status | ready / blocked / not_evaluated | 当前具体任务能否通过应用检查；无任务时为 not_evaluated |
| freshness | current / stale / unknown | 历史报告是否仍对应当前本地事实；不是历史结论的改写 |
| execution_readiness | not_verified | 没有声称现场执行已验证 |

“可准备任务”不等于“已可生成”。
机器人检查页面显示准备状态与具体骨架缺项，包含类别／关系／数据属性、规范标识和中文名；关系同时显示定义域、值域，数据属性显示类型。
freshness 详细值可从 API 读取；旧报告保持历史结论，页面可从其实体引用显示缺项标识，重新检查后取得新的完整诊断。
旧 compatibility 的 supported／unsupported 是准备状态投影，不是执行许可。
旧报告、其他应用报告或客户端 ready 标记都不能授权生成。

四层结构为 schema、execution、task、environment。
公共核心支持检查依赖、精确 issue、修复 action 和完整版本依据。
机器人领域检查器校验 TBox 角色语义、执行结构、任务缺口和当前策略要求的适配依据，并复用原消费者及最终生成守卫。
同版本多应用能力已有合成核心测试，data.deep_qa 尚未接入公共检查服务，注册可见但 availability=unavailable。
现有深度问数请求链路没有在本轮迁移。

## HTTP 调用

所有入口复用平台 Host、Origin、JSON 和体积限制。
以下占位 ID 必须替换为当前服务中真实的固定资产／版本／任务 ID；version_id 不接受 latest。

### 静态注册描述

`GET /api/applications`

返回 `items` 数组，公开 application_id、contract_version、entrypoints、availability。
默认登记 robotics.pick_place（robotics.generate_behavior_tree）和尚未接通的 data.deep_qa（data.ask）。
该请求不检查本体、不创建域库、不序列化 Python 检查器或修复器。

### 显式检查

`POST /api/application-checks`

```json
{
  "entrypoint": "robotics.generate_behavior_tree",
  "asset_refs": [{"ontology_id": "<ontology_id>", "version_id": "<version_id>"}],
  "context": {"mode": "real"},
  "request_id": "review-check-001"
}
```

首次返回 201，结构为 `{report, freshness, replayed}`。
report 含 check_id、checked_at、application_id、contract_version、asset_refs、payload_hashes、task_ref、task_hash、context、environment_revision、basis、layers、issues、allowed_actions 和三类判断字段。
四层的 checks 保留每项结果；issues 使用稳定 code，并保留关联实体、证据和修复建议。
无任务时 task_ref=null、execution_status=not_evaluated。
真实接口取得的完整样例见 [T06 HTTP 样例](audits/IR-025-t06-http-examples.json)，其中资产只存在于隔离测试库，不能直接复用其 ID。

同 request_id、相同输入返回 200／replayed=true，不重新运行检查。
同 ID 不同输入返回 409/request_conflict；主动复查必须使用新 ID。
已有任务可用 `context.task_ref={"session_id":"<session_id>"}`，任务内容和 revision 由服务端读取。
不要提交 generation_policy、task_hash、ready、contract_version、payload、任意路径或处理器配置。

### 历史报告

`GET /api/application-checks/<check_id>`

返回 `{report, freshness}`，不重新运行检查器或探测网络。
若不能通过本地事实确认当前依据，freshness=unknown；已知变化为 stale。
报告保留原结论和摘要，旧报告不会因读取或重放变为新授权。

`GET /api/ontology-assets/<ontology_id>/versions/<version_id>/compatibility`

读取最新历史摘要；无记录为 status=not_assessed，有记录附 check_id、freshness 和 reasons。
目录、详情、图谱也读取此类摘要，不即时运行消费者。

### 修复分发

`POST /api/application-checks/<check_id>/repairs`

```json
{
  "issue_id": "<issue_id>",
  "action_id": "<registered_action_id>",
  "input": {},
  "expected_revision": 3,
  "request_id": "repair-001"
}
```

issue/action 必须来自对应报告；expected_revision 对应 action.target 指定的资源修订，不是报告号或本体版本号。
导航返回 200，受理运行返回 202；结果包含 kind、target、return_context、replayed，以及 href 或 run_id。
GET 不执行动作；重复请求重放已有受理结果，不重复产生本地事务效果。
未实现动作为 409/repair_unavailable；未知动作为 400/unknown_action；过期或资源修订变化为 409/stale_result。
IR-026 已注册三类机器人修复：修订本体骨架、补齐执行结构、补齐任务信息。
执行结构修复在用户明确点击后尝试自动匹配；规范词表及角色语义校验通过时，后端直接入队并导航到进度页。
无法确定对应时才展示人工选择，点击一次“确认并生成应用版本”即可原子保存映射并提交；GET、刷新和只读检查不启动生成。
业务修复返回同一任务的原有对象／配置表单，不自动制造零件、工位、载具或槽位。

### 常见错误与运行边界

| 状态／代码 | 含义 |
|---|---|
| 400/invalid_request、unknown_entrypoint | 字段、固定引用或入口不合法 |
| 404/not_found | 资产、版本、任务或报告不存在 |
| 409/request_conflict | 幂等身份对应了不同输入 |
| 409/stale_result、repair_unavailable | 报告／目标已变化，或修复尚未接通 |
| 503/storage_unavailable | 读取或原子保存失败，不返回虚假报告号 |
| 503/application_unavailable | 功能关闭或应用适配器尚未接通 |

平台原 Host／Origin／非对象 JSON 前置守卫保持既有错误结构。
合法输入产生缺项、不可支持、内容不合法或检查器 error，仍可返回 201 保存诊断；不表示 ready。
机器人入口的开关沿用 DATAMIND_ROBOTICS_ENABLED；历史读取不因关闭写功能而被隐式重新计算。

## 存储与升级

IR-025 的 schema 7 新增 application_checks、application_check_requests、application_repair_requests。
IR-026 将域库增量升级至 schema 8，新增 robotics_adaptations、robotics_adaptation_requests、robotics_application_documents；仅为适配运行扩展输出引用约束。
报告与幂等身份同事务提交；修复本地受理记录和幂等结果同事务提交，失败回滚。
初始化写路径可以升级旧库，GET 不创建库或迁移；不能读取旧 schema 时返回 storage_unavailable。
升级保留原有数据字段和工件字节，schema 5→6 的既有迁移允许新增 design_input 列。
上线前保存配套数据库和工件备份；旧代码若只支持 schema 6／7，回退时恢复配套备份库，不只修改 PRAGMA 版本号来伪装降级。

## 机器人应用版本的使用

构建仍沿用通用 TBox＋ABox，确认完成即可从本体库找到本体，不选择应用、不自动执行应用检查。
点击“生成行为树”后查看诊断；缺角色对应可先核对已有术语，缺 TBox 内容返回原设计，缺执行字段进入角色对应与适配页面。
点击“修订本体骨架”会携带该报告，当前阶段的简短修改建议直接显示在“建模要求”输入框的浅色占位文字中，例如补充 ImplementationConfiguration 或 cppType。
修订页不显示额外缺项卡片、长篇说明、建议预览或填入按钮；在输入框按 Tab 填入建议，可自行编辑后点击“确认并发送建模要求”。
已有自定义文本时 Tab 保持正常焦点行为，Shift+Tab 正常向前移动；填入建议、打开页面和刷新均不调用模型。
修订按钮按全部缺项定位阶段：缺类别时打开类别修订；只有关系或数据属性缺项（如 cppType）且类别已确认时，直接打开关系与属性修订并保留类别确认。
类别和属性同时缺失时先修类别，确认后再补属性；当前类别尚未确认时仍须先确认类别。
点击修订会打开对应阶段的新草稿，不调用模型；Tab 填入建议后，仍需“确认并发送建模要求”，完成后再确认骨架并重新提取实例。
旧资产版本和工件保持不变；刷新修订页只读取当前草稿，不重复切换阶段或创建新修订。
模型排队及生成期间显示等待动画与当前状态；实例构建显示独立的构建提示，不标为模型生成。
设计完成后，“确认类别”或“确认骨架并构建实例”显示在顶部，滚动查看结果时仍可操作。
本轮已处理的浅色建议清除，输入区域收起为“继续提修改意见”；展开后可继续编辑并发送，确认进入下一阶段后再显示该阶段的建议。
此前报告仅作为修订参考，修改完成后需要重新检查；不同设计的报告不能用于当前输入框的补全。
点击“补齐执行结构”后，已知词表中的规范 ID 自动对应实际完整 IRI，并校验实体种类、类别层次、关系方向／范围和数据类型；通过后自动生成应用版本，无需再次确认。
自定义名称不按相似度或中文名猜测；例如 CallSite 不会自动认定为 SkillInvocation，需由用户选择实际对应。
无法确定或词表版本需要核对时才展示选择界面，已确定的对应保留；填写后一次“确认并生成应用版本”即可，不再拆成两个按钮。
自动／人工方式、规则版本及实际映射保存在运行和完整 JSON 的追溯信息中；低层分步确认接口保留兼容，公开准备接口默认尝试自动匹配。
执行适配沿用后台持久队列，不调用模型；失败保留诊断，修正输入后显式重试，刷新不会重跑。
成功后保存同资产的新候选版本，旧设计和旧下载字节保持不变。
新版本可下载 OWL 和一个完整 design.json，包含当前事实、原设计／消息／来源、身份映射、适配依据、补全记录及版本血缘。
对象创建、配置关联、参数和确认保存后会继续派生新版本，同步更新总 JSON 与 OWL。
原设计的构建 JSON 标识仍为 robotics-design v1；应用总 JSON 使用 robotics-application-design v2，旧文件不就地升级。
角色／执行结构适配成功后，复检并进入任务；业务对象、载具／槽位、感知／放置配置沿用原有任务表单补足。
从原任务发起修复时，生成新版本后明确“切换并返回原任务”；任务有并发修改时拒绝迟到切换，保留原输入。
strict 先补全业务知识，再提供当前范围的适配依据；offline 维持原候选策略，生成结果均不代表现场执行已验证。
成功保存的应用版本自包含：后续准备、补全、复检、生成和历史读取不需要原始双 XML；重新提取原设计仍需原来源。
自包含不放宽工件完整性检查；JSON／OWL 损坏阻止读取或生成，适配附件完整性与撤销规则仍约束任务准备和生成。

## 工件升级与兼容对照

| 资产类型 | 当前用途 | 升级与保存方式 |
|---|---|---|
| 通用 `robotics-design` v1 | 保存用户确认的 TBox、ABox、来源与设计历史，允许暂不满足应用要求 | 保留原 OWL／完整 JSON 字节；显式适配后派生应用 v2，不就地修改原文件 |
| 旧 UR 本体 | 沿用既有消费者及任务生成链路 | 不要求先转为新设计工件；仍按原 offline／strict 规则检查 |
| `robotics-application-design` v2 | 保存可供现有行为树消费者使用的执行结构及当前业务事实 | 同资产派生新候选版本；每次任务补全保存新版本并同步 OWL 和单个完整 JSON |

工件版本、域库 schema 和本体版本分别表示文件格式、存储结构和资产历史，不互相替代。
自动生成 v2 后仍需检查当前任务的对象、配置和适配依据；`preparation_status=available` 只表示可进入任务准备。
旧 v1 的重新提取／适配仍需原始双 XML；成功生成的 v2 在后续任务准备、补全、生成及历史读取时不再依赖原双 XML。
下载时选择同一固定版本的 OWL 和完整 JSON，不能将父版本 v1 JSON 与子版本 v2 OWL 混用。
回退应恢复升级前匹配的数据库及工件备份，并保留回退后新版本的独立备份；不覆盖旧文件或删除新版本来伪装格式降级。

## 后续交接

- IR-026 T06：真实副本、新旧 UR 对照和全量回归均已通过，已由用户提交指令确认交付；见 [交付评审](audits/IR-026-t06-delivery-review.md)。
- IR-027：现有深度问数接入、数据连接与编辑／缓存新鲜性。
- IR-028：删除恢复、生命周期修订与执行受理保护。
- IR-029：完整本体详情、历史状态呈现与修复导航体验。
