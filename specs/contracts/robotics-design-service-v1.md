# 设计会话与候选服务测试接口 v1 — DR-070 / IR-024 T06

- 日期：2026-11-10。
- 依据：[已确认 DR-070](../decisions/DR-070-robotics-dual-build.md)、[IR-024](../iterations/IR-024-robotics-dual-build.md)、[T01 接口](robotics-dual-build-v1.md)、[T04 核心接口](robotics-modeling-core-v1.md)。
- 本文件细化已确认行为的 Python 服务接点；用户于 2026-11-10 确认推进 T07，82 项测试已转绿，完整回归 12 阶段全部通过。
- 不增加无 XML 输入、不更换平台模型配置、不重构已有任务模型客户端。

## 服务与后台执行边界

新增 `domain_runs.robotics_design_service.DesignService(root, *, route_resolver=None, max_pending=4, read_only=False)`。
root 为显式绝对资产目录，使用现有 `domain.sqlite3`、来源库、RunStore 和 AssetStore。
route_resolver 为宿主内部注入，与 T03 协议相同；默认复用平台配置，HTTP 不接受此参数。
不要求调用方构造名为 BuildRoute 的对象，但解析所得对象须满足已冻结的 public/request 协议。
只读构造、查询和导出不能解析模型配置、发模型请求、升级旧数据库、恢复运行或创建缺失目录。

| 方法 | 输入与结果 |
|---|---|
| `submit(**args)` | 与 BuildService.submit 相同的 name、recipe、profile_id、request_id、sources、description；返回设计准入回执 |
| `get(session_id)` | 当前会话投影，独立副本 |
| `list_revisions(session_id)` | 按 revision 递增的不可变快照索引，包含 revision |
| `get_revision(session_id, revision)` | 固定 JSON 修订封套，至少含 session_ref、revision、state |
| `export_revision(session_id, revision, *, format)` | 固定 bytes；format=json / owl，JSON 与 get_revision 封套一致 |
| `message(session_id, *, text, request_id, expected_revision, expected_stage)` | 校验身份与阶段后排队 design_tbox，返回准入回执 |
| `confirm_classes(session_id, *, request_id, expected_revision, snapshot_sha256)` | 本地确认类别；进入 properties，不请求模型 |
| `edit_classes(session_id, *, request_id, expected_revision)` | 撤销当前确认及当前输出引用，保留全部历史 |
| `confirm_schema(session_id, *, request_id, expected_revision, snapshot_sha256)` | 本地确认骨架并原子排队 extract_abox |
| `retry(session_id, *, request_id, expected_revision, failed_run_id)` | 仅显式重试同会话、未过期的失败／中断运行；保留 retry_of |
| `claim_next()` | SQLite 原子领取 FIFO queued 设计运行并置 running；没有任务返回 None |
| `execute(run_id)` | 宿主工作进程执行已领取的运行；成功或正常业务失败返回最终运行记录 |
| `recover_interrupted()` | 宿主启动时显式执行；只中断设计类 running 运行，不重放模型；返回受影响 ID |

上述 claim/execute 是服务测试与宿主调度接点，不作为 HTTP 端点暴露。
工作进程取得模型响应后必须重新校验 run 状态、会话绑定、来源摘要及修订，恢复后的旧工作进程不能覆盖新重试。
同一运行仅由领取者执行；终态运行不能再次执行。
恢复属于单宿主启动控制，不是日常查询副作用；测试模拟失联工作进程后迟到的响应，验证其丢弃结果。
模型等待期间不持有 SQLite 写事务或全局 RDF 锁。
本组检查服务级原子领取和中断恢复，真实后台调度、HTTP 进度及浏览器闭环仍由 T08/T09 集成验证。

## 会话、修订和幂等

会话投影至少包含 session_id、session_ref、method、model、stage、revision、status、run_id、result、snapshot_sha256、class_confirmation、schema_confirmation、source_sha256、output_asset_ref。
session_ref 为 `{namespace: "robotics_design", id: session_id}`，method 固定 llm_tbox_abox，model 仅保存公开身份。
来源在准入时固定，完整词表、证据及提示词身份可由持久快照恢复，不依赖 Python 内存。
初始 revision=0，空 description 时 status=needs_description、run_id=null，保存两份来源但不占队列。
非空 description 排队第一轮；每个有效 TBox 结果、本地确认、返回类别各推进一个修订。
排队、领取、失败及重试不推进有效修订；每个成功修订只保存一次。
类别确认进入 properties/awaiting_input，后续 message 启动属性设计。
成功模型轮次进入 awaiting_confirmation；确认骨架进入 abox，抽取运行绑定该确认修订，不另造 TBox 修订。
最终抽取成功为 completed；失败／中断分别为 failed/interrupted，保持上一有效结果与修订。

准入与操作回执固定保存；相同 request_id 和语义输入重放原回执，不重新路由、不重复模型请求、不重复来源或版本。
返回原排队回执不代表当前仍排队，最新状态由 get 查询。
提交请求 ID 为构建入口范围；后续操作 ID 为会话范围，操作类型也是指纹的一部分。
同 ID 改变输入或操作返回 request_conflict；不同会话可使用同一个操作 ID。
确认绑定当前 revision 与 snapshot_sha256，文本消息不能代替结构化确认。
单会话只允许一个待处理／运行中的操作；并发确认与发消息不能都成功。
queue_full 不留下来源、会话、请求或运行的半提交；needs_description 与等待确认不占运行队列。

错误采用可公开的 code、status_code（及需要时 issues）：invalid_request=400，not_found=404；request_conflict、revision_conflict、stage_conflict、snapshot_conflict、confirmation_required、session_busy、retry_conflict、source_integrity、configuration_changed=409；queue_full=429。
重复操作先按指纹重放，再检查当前 revision；非重放的新操作才做当前状态校验。
重试外部会话或成功运行返回 retry_conflict；已有活动重试返回 session_busy。
已排队后配置变化必须在请求模型前失败；同端点／同模型凭据轮换可重试。
正常模型／语义／来源失败保存运行 result.error.code，并保留原修订；敏感底层错误不进入公开消息或持久日志。
磁盘完全不可写时允许抛 OSError/save_failed，或事务失败抛 SQLite 错误；不得伪装为 completed，后续由恢复与显式重试处理。

## 数据库与完成契约

从真实 schema=5 迁移到显式新版本，添加设计会话／修订／请求存储及 runs.design_input。
保持旧 runs 字段、事件、manifest、文件字节、任务会话及旧 CHECK 行为。
新设计运行 operation 为 design_tbox 或 extract_abox，mode=real，design_input 非空，旧 build_input 与 ontology_id/version_id/ontology_snapshot 为空。
SQL CHECK 不允许无输入运行、混合旧 build_input 与 design_input，或把设计输入用于其他 operation / demo 模式。
RunStore.start／manifest／读取支持设计分支，但旧通用 finish 与 finish_build 不能绕过设计完成验证。
运行 design_input 固定来源摘要、词表摘要、修订及操作身份；extract_abox 另绑定确认摘要与映射摘要。
components 保留非敏感模型身份／invoked、prompt_version 等字段。
模型调用、JSON 格式修正和本地语义验证沿用已通过测试的 T05 核心。

模型修订工件包含 design.json、tbox.owl 及 manifest；本地确认／编辑快照也必须有固定 JSON 表示。
按 2026-11-10 用户补充要求，最终成功运行工件固定为 candidate.owl、design.json、manifest.json。
design.json 是完整业务快照，不能将实例、覆盖或校验报告再拆成 abox.json、coverage.json、validation.json 分别保存。
它保留修订封套的 session_ref、revision、state，并内嵌 abox、coverage、validation、components、output_asset_ref、payload_sha256。
state 完整包含本轮 TBox 设计／确认、建模消息、能力问题、待明确项、词表快照、来源文档信息与证据，不得只留下需要再下载其他 JSON 才能解析的业务引用。
coverage 内含映射版本摘要与遗漏报告；validation 对应本次运行实际完成结果；payload_sha256 绑定 OWL 字节。
仅凭此 JSON 的骨架、实例和证据就能重新导出语义同构的完整 OWL，无需另取 ABox 或报告文件。
原始 XML 字节仍作为输入来源保管，JSON 保留其身份／摘要及解析证据；不要求把上传文件原始字节全部嵌入 JSON。
manifest.json 仅为平台内部完整性登记，非用户需配套下载的业务分片；总 JSON 不包含自身或 manifest 的摘要，以避免循环摘要。
总 JSON 与 OWL 同次提交、不可变保存；下载直接返回已登记的原始 JSON 字节，不在下载时拼接未固定的当前数据。
manifest 绑定 session_ref、design_input、output_asset_ref、result 和每件文件的摘要／长度。
JSON 修订封套 state 保存对应修订的完整核心状态；会话当前运行状态可以变化，不能反写历史文件。
TBox 阶段 JSON 已包含该阶段的本体定义和辅助信息，尚未生成的 ABox 可为空或不出现。
确认骨架修订与最终抽取结果是两个不同的固定快照；最终运行的完整 design.json 新建在该运行下，不能覆盖确认时已下载的历史 JSON。
同一阶段／结果的业务内容只保存在一个完整 JSON 中，保留多次历史快照不等于拆分一个结果。
最终结果 status=designed、contract=`{id: "robotics-design", version: 1}`，execution_readiness=not_verified，当前样例 task_readiness=unsupported。
资产版本使用 robotics_rdf / candidate，metadata 包含 design_contract、session_ref、design_revision、build_run_id。
设计语义、来源与实际 RDF 字节验证后，版本、工件和成功运行在同一可见事务中登记。
任一写入或提交失败都不得推进有效指针或留下可见成功资产；回滚清除事务新建的未登记文件。
后续编辑清空当前 output_asset_ref，已存版本仍可访问；再次抽取创建同一本体的子版本，parent_version_id 指向上一版本。

## 资产读取与应用准入

新增内部读取接点 `ontology_assets.robotics_design.DesignAssets(root, *, read_only=True)`。
`detail(ontology_id, version_id)` 返回 version、tbox、abox、report 等固定版本内容，验证载荷、manifest、来源及修订引用。
`payload(ontology_id, version_id)` 返回与 AssetStore.read_payload 一致的原始 OWL 字节。
`export_json(ontology_id, version_id)` 验证固定版本及工件完整性后返回对应完整 design.json 的原始字节。
T08/T09 的最终版本页面提供“完整 JSON”和“OWL”两个主要下载选项，不要求用户拼接实例与报告文件。
`graph(ontology_id, version_id)` 提供 nodes/edges 通用设计视图，节点至少区分类与实例，不能调用 UR 专用结构读取器代替。
`compatibility(ontology_id, version_id)` 返回 status、reasons、payload_sha256，status 区分 supported / unsupported / invalid。
已验证但不满足 UR 消费契约的设计版本为 unsupported；损坏字节或交叉修订元数据为 invalid；旧合法 UR 版本为 supported。
资格绑定实际版本摘要，不能信任客户端写入的 supported／artifact_contract 标签。
该兼容结果供 T08/T09 的目录、详情和应用页面使用；本组仅验证内部投影和现有任务后端防绕过，不声称完成 UI 过滤。
既有任务 API/TaskService 仍对实际固定版本执行原消费检查，拒绝不兼容候选且不留下任务／运行／模型调用。
既有旧资产详情与下载、原旧构建完成契约和离线任务策略保留。

## 本组测试材料与限制

使用两份真实 UR XML、T04 裁剪的已选 TBox、T05 包内词表和真实抽取／导出，模型只由合成 runtime.run_turn 返回结构化回复。
网络 tripwire 禁止外部连接，未替换数据库、领域验证器或构建成功响应。
迁移夹具 `tests/fixtures/domain_storage/schema5_snapshot.json` 在 T07 前由旧代码实际升级 schema4 并创建合成旧任务后冻结，随后测试直接重放 SQL/行/文件，不调用新版初始化器伪造 schema5。
RED 失败可能停在缺失模块之前，后续断言尚未执行；T07 必须逐项跑通，不能将本轮失败记录当作通过证据。
完整 HTTP 请求体、功能开关、同页 UI、目录展示和真实浏览器覆盖属于 T08；真实模型质量属于 T10。


## T06 评审修订：统一总 JSON（2026-11-10）

用户明确不要求来源工程 JSON 字段兼容，但要求本体及辅助信息统一保存为总 JSON。
据此替换原多业务工件设计，并调整尚待人审的 T06 工件集合、语义一致性、缺口报告保存、历史 JSON 不变及文件故障断言。
这是实现前的需求修订，不修改 T03/T05 已审阅测试，也不提前进入 T07。
