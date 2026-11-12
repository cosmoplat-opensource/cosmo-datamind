# 应用契约使用与接口说明 — DR-071 / IR-025

适用版本：IR-025 T05 已实现，T06 交付评审。
本说明描述已实现的公共能力；机器人精细适配与修复、深度问数接入分别由 IR-026、IR-027 完成。

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
   当前默认适配器能判断现有行为树消费者是否兼容，不能精确列出所有缺失类别或自动补齐。
   默认机器人修复处理器尚未注册，没有自动修复入口；精细定位和返回补全流程由 IR-026 接通。
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
默认最小页面主要显示准备状态和问题文本；freshness 详细值可从 API 读取，完整视觉和诊断导航在后续体验迭代中完善。
旧 compatibility 的 supported／unsupported 是准备状态投影，不是执行许可。
旧报告、其他应用报告或客户端 ready 标记都不能授权生成。

四层结构为 schema、execution、task、environment。
公共核心支持检查依赖、精确 issue、修复 action 和完整版本依据。
当前机器人默认实现主要复用现有消费者及任务缺口检查；schema/environment 的精细规则由领域适配后续补充。
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
当前默认机器人没有已注册修复动作；此协议的导航／受理成功由明确标注的合成事务处理器验证。

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

域库增量升级为 schema 7，新增 application_checks、application_check_requests、application_repair_requests。
报告与幂等身份同事务提交；修复本地受理记录和幂等结果同事务提交，失败回滚。
初始化写路径可以升级旧库，GET 不创建库或迁移；不能读取旧 schema 时返回 storage_unavailable。
升级保留原有数据字段和工件字节，schema 5→6 的既有迁移允许新增 design_input 列。
旧代码若只支持 schema 6，回退时需恢复配套备份库；不要只修改 PRAGMA 版本号来伪装降级。

## 后续交接

- IR-026：机器人角色清单、执行知识适配、精确缺项和 TBox／ABox／业务补全修复闭环。
- IR-027：现有深度问数接入、数据连接与编辑／缓存新鲜性。
- IR-028：删除恢复、生命周期修订与执行受理保护。
- IR-029：完整本体详情、历史状态呈现与修复导航体验。
