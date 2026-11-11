# 机器人双路径构建契约 v1 — DR-070 / IR-024 T01

- 冻结日期：2026-11-10。
- 依据：[已确认 DR-070](../decisions/DR-070-robotics-dual-build.md) 与 [IR-024](../iterations/IR-024-robotics-dual-build.md)。
- 本文件冻结实现接口，不表示功能已经实现；T02 仅覆盖路由、共同准入及原离线路径，设计持久化和完整 UI 按后续任务验证。

## 1. 平台模型路由接口

新增模块 `domain_runs/robotics_build_routing.py`。
`resolve_route(*, config=None, environ=None, registry=None)` 读取一次有效配置快照，返回 BuildRoute。
省略参数时分别读取平台 `_load_engine_cfg()`、当前进程环境及 `agent_runtime`，显式注入只用于内部组合和测试，不接受 HTTP 提供运行时对象。
registry 沿用 `available()` 与 `get_runtime(name)`，运行时沿用 `run_turn(session_id, message, timeout=..., model=...)` 协议。
适配支持平台现有 openai、hermes、claude-code、openclaw 文本运行时；没有可调用 run_turn 的对象不具备本构建能力。
只有真正未配置且注册表没有可用模型时返回离线路由。
显式但无效的 driver、部分端点配置、无效地址／数值参数、配置齐全却未注册、已移除配置但残留注册及配置与旧运行时身份不符均为 configuration_error。
不在路由判定中调用 run_turn 或发网络测试请求。
未配置模型的环境中，平台缺省的 hermes 偏好不是用户显式配置，不能因此阻止离线使用。

OpenAI 配置使用 DATAMIND_LLM_BASE/KEY/MODEL/TIMEOUT/MAX_TOKENS 与平台保存的 llm 字段。
非空环境变量优先于保存配置，空值沿用平台的配置回退规则。
base 与 key 必须齐备；model 省略时沿用现有平台默认值，不增加第二套默认模型规则。
端点只接受不带用户凭据、query 或 fragment 的 HTTP(S) 基础地址，超时和 token 限额沿用平台类型与有效范围。
模型路由不得消费 TBOX_LLM_* 或 ABOX_LLM_*。
多个受支持运行时的顺序遵循有效 CLAW_DRIVER／保存 driver 优先，其余按平台 openai、hermes、claude-code、openclaw 顺序。
运行时身份必须由适配器读取实际配置，不能把注册表“存在”当作外部模型在线的证明。

`BuildRoute.public()` 返回以下两种结构，并每次返回独立副本：

```json
{"method":"legacy_xml","model":null}
```

```json
{"method":"llm_tbox_abox","model":{"provider":"openai","model":"configured-model","configuration_id":"opaque-stable-id"}}
```

configuration_id 是 provider、有效模型及端点／运行时提供方身份的确定性非敏感摘要。
密钥及密钥摘要不参加持久化身份；同模型、同端点的密钥轮换允许重试，但每轮必须从当前有效配置取得凭据。
公开结构不返回密钥、原始端点或整个运行时对象。
配置变更不会反向修改已经保存的 public 快照。

`BuildRoute.request(session_id, prompt, *, timeout)` 只调用已经选定的一个运行时一次，成功返回原始响应正文。
这里只处理调用边界，不解析 TBox、不写本体、不执行格式修正或调用旧构建器。
格式修正预算和 TBox 语义验证由 T04/T05 的建模层负责。
运行时返回失败、空白正文或抛出异常时抛 BuildRequestError，code=model_request_failed，公共消息不泄漏凭据。
离线路由调用 request 时抛 model_not_available。
不尝试其他模型，不把错误转成 legacy_xml 或构建成功。

`require_same_route(pinned_public, current_public)` 对 method 与模型身份进行兼容检查。
身份变化或模型消失时抛 configuration_changed（HTTP 409），不改写两个输入。
相同身份恢复后允许重试，真正的重复版本防护由持久化幂等层负责。

## 2. 共同输入准入

复用 `POST /api/robotics/builds`，必需表单字段保持 name、recipe、profile、request_id。
recipe=bt_xml、profile=ur_pick_place 保持旧客户端兼容；它标识本轮 UR 领域与旧模板回退，不把旧固定步骤验证强加给新 TBox 设计。
新增可选 description（UTF-8 文本，最多 20000 字符），文件字段仍须且仅须 behavior_tree、node_models 两个角色，每个角色一个非空 XML。
外部客户端不能指定 method、driver、model、base_url、api_key 或传入任意文件系统路径作为来源。
文件大小沿用配置的单文件限额和 HTTP 总请求限额。
共同层在入队、来源登记和模型调用前检查可解析 XML、DTD／实体声明、最多 20000 节点与最多 100 层嵌套。
UTF-16/32 包装不得绕过实体声明检查；允许的实际编码由安全解析器显式处理，不能把解码异常泄漏为 500。
共同层还检查两种来源的角色语义，模型路径不调用旧 UR 全模板校验代替通用来源检查。
旧路径在后台继续执行原模板和数据流检查，保持原领域失败报告。

| 结果 | HTTP / code | 可观察副作用 |
|---|---|---|
| 功能关闭 | 503 / feature_disabled | 不初始化资产库、不探测模型 |
| 必填项缺失、空文件、重复或额外字段 | 400 / invalid_request | 不登记来源或运行 |
| XML 解析或安全限制失败 | 400 / invalid_source | error.issues 包含 source_role、位置和原因，无半提交 |
| 大小超限 | 413 / file_too_large 或现有请求体错误 | 无半提交 |
| 配置问题 | 503 / configuration_error | 不登记成功构建或自动离线运行 |
| 请求编号冲突 | 409 / request_conflict | 原结果不变 |
| 排队容量不足 | 429 / queue_full | 无新运行 |

请求接收后冻结 method，重放相同 request_id 必须返回原准入结果，不能因配置改变重新分流。
配置在准入后失效，不影响旧离线运行，但不能使模型会话偷偷降级。
同 request_id 的 description、XML 或其他语义输入改变视为冲突。

## 3. 准入响应、设计操作和读取

离线准入保持原 202 的完整运行响应，追加 method=legacy_xml、model=null，原 run_id、build_input、status、output_asset_ref 等字段含义不变。
旧历史没有新增字段时由读取层按原 operation 兼容解释，不回写旧工件字节。
模型准入返回 method=llm_tbox_abox、model、session_ref、stage、revision，以及当前 run_id（尚未排队时为 null）。
模型路径 description 为空时创建等待输入的设计会话，status=needs_description、stage=classes，不占运行槽位、不调用模型。
description 非空时创建第一轮 design_tbox 运行并返回 202；确认修订和最终 ABox 操作分别建立后续运行。
模型准入及会话的实际持久化、响应幂等归属 T06/T07；T03 只完成可独立验证的路由及旧路径接点，不提前对 UI 宣称模型构建闭环可用。

| 接口 | 请求要点 |
|---|---|
| GET /api/robotics/capabilities | 保留旧 recipes/limits/enabled；增加只读 building_method/model/configuration_issue，不作网络测试 |
| GET /api/robotics/design-sessions/<id> | 返回阶段、revision、确认基线、当前有效修订、运行引用及问题 |
| GET /api/robotics/design-sessions/<id>/revisions/<revision> | 固定历史快照，不调用模型或改变当前修订 |
| GET /api/robotics/design-sessions/<id>/revisions/<revision>/exports/<format> | format=json 或 owl，成功修订的固定导出 |
| POST .../<id>/messages | text、request_id、expected_revision、expected_stage |
| POST .../<id>/confirm-classes | request_id、expected_revision、snapshot_sha256 |
| POST .../<id>/edit-classes | request_id、expected_revision；仅本地撤销确认 |
| POST .../<id>/confirm-schema | request_id、expected_revision、snapshot_sha256；规则构建 ABox |
| POST .../<id>/retry | request_id、expected_revision、failed_run_id；固定会话、来源和模型身份 |

新接口的 schema、只读行为及所有冲突断言在 T06/T08 编写，不以 T02 覆盖上述全部接口。
确认动作不能通过 messages 文本代替；新 XML 不能随后续消息加入原会话。
TBox 修订 JSON 与 OWL 是同一不可变修订的两种表示，最终 ABox 使用独立设计候选完成契约写入资产库。
2026-11-10 用户补充：JSON 必须是含本体设计、已生成实例及辅助信息的完整快照，不能拆成多份业务 JSON 分开保存。
TBox 阶段与最终候选各提供其对应的完整 JSON 和 OWL；最终候选 JSON 的字节导出接点见设计服务契约 export_json，HTTP 路由在 T08 细化。

新增运行 design_tbox、extract_abox 采用独立结果契约及显式 schema 迁移；不放宽旧 build_ontology 完成校验。

## 4. T02 测试边界

测试只替换平台配置、运行时注册表和外部模型调用边界，不伪造 XML 抽取、OWL 校验、数据库写入或 HTTP 成功。
无网络 fixture 阻止意外向任何模型服务发请求，合成响应仅验证路由和错误处理，不代表模型建模质量。
真实 XML 通过原 importer、publisher、consumer、资产持久化和下载路径构成兼容基线。
模型失败后持久版本保护、格式修正上限、会话重启及完整模型 UI 仍由后续 RED 组验证，不能因本组通过就标记整条验收完成。
