# 深度问数应用兼容 v1 — DR-156 / IR-028 T01

日期：2026-11-16。
用户授权完成 T01～T03，T02 后直接实现；本规约冻结本阶段实现边界。

## 兼容矩阵

| 输入与能力 | 保留行为 | 诊断规则 |
|---|---|---|
| 没选图谱 | 使用 demo | 记录实际图谱 |
| 单个不存在的旧 key | 沿用已有 demo fallback | 保留明确 warning，不假称使用原图 |
| 多图 | 旧顺序、去重和合并规则 | 有序版本向量；不扩张 SQL 范围 |
| 人工编辑 | load_ir_edited 有效结果 | 摘要取实际内容，不取秒级 mtime |
| table / tables[] / 上传表 | 复用现有画像 | 上传表归一 up.；不修改原 IR |
| 部分可用 | 可问可用子集 | 不可用表警告，具体 SQL 使用时阻断 |
| 全部不可用／未绑定 | 保留原范围判断 | 连接／绑定修复定位；不得进入 SQL |
| 普通计数，无指标 | 继续支持 | 不增加指标认证门槛 |
| certified / verified / candidate / heuristic 指标 | 现有卡片、排序、口径及离线规划 | 不统一升级认证，不改变 MCP 门槛 |
| 无模型 | 现有 fallback_plan | 适配器不调用模型或执行 SQL |
| 固定资产版本 | 只读 AssetStore 的 datamind_ir | 机器人／损坏载荷先拒绝，不进入 SQL 检查 |

## T03 接口

`DataQAAdapter(host, assets=None)` 接受服务端宿主和可选只读资产存储。
`resolve(graph_keys=None, asset_refs=None, question=None, plan=None, focus_tables=None)` 返回公共 evaluate 使用的 snapshots/task/context/environment。
旧 key 与固定版本引用互斥；同一固定版本不得重复，latest 禁止；空旧 key 默认 demo。
旧 key 通过宿主 _anchor_ir 和 load_ir_edited 解析；每个有效输入单独冻结规范 JSON 字节，构造内容寻址 version_id。
固定资产沿用真实 ontology_id/version_id 与工件完整性校验；未登记图读取不建库、不入库。
有效载荷、按顺序的图谱引用、最终合并 IR、实际连接目录及宿主连接修订指纹进入依据；内容不变不依赖采样时间。
`evaluate(**kwargs)` 只读诊断；`offline_plan(data)` 调用原离线规划器；报告不是 SQL 执行许可。
默认公共 registry 的 data.ask 仍 unavailable；`registry(data_qa=adapter)` 才绑定专用 data.deep_qa 描述，不能继承机器人 checker／executor／repair handler。

## 分层检查与修复

schema 复用 _qa_graph_profile；execution 判定可查询子集；环境层提供丢失表警告及定位。
所有表不可用与未绑定阻断准备；部分不可用仅警告。
当前问题与计划进入 task 快照；无问题不评估执行，有问题无计划明确 not_evaluated，不误报 ready。
计划最多四条，逐条经 sql_is_readonly、_single_statement、_qa_validate_sql；范围归一与原 _qa_anchor_ir 一致，显式 focus 只能缩小已选本体。
指标卡复用 _metric_cards（其内部复用 metric_contract），不另发起指标执行或认证。
修复定位包含不可用表／未绑定对象及固定图谱引用，给出“接入并物化数据表”或“完成对象与表绑定”的建议。
T03 仅只读建议，不注册伪修复写操作或自造跳转页面；导航接通留给 T05。
检查异常使用公共 system.checker_failed，不暴露连接路径、SQL 异常或凭据；失败不冒充缺失。

## 后续协议与范围

T04/T05 将同步 /api/chat 与 /api/chat/stream 接同一预检，保留旧请求及响应字段、事件顺序，错误增量增加结构化缺项。
缓存命中前复核，键包含有序输入、有效 IR、契约版本、连接和生命周期修订；删除 key 不得 demo fallback。
T03 不启用上述入口／缓存／生命周期改动，不声称已完成 DQC-01～04 全部验收。
IR-029 提供生命周期写能力；本轮不造删除功能，不改旧数据构建流程。

## 验证

T02 使用真实编辑日志与临时 SQLite、真实公共 evaluate 和原 SQL／指标／离线函数；禁止模型调用和 SQL 业务执行。
先记录 RED，再冻结测试摘要，T03 不通过修改已冻结测试求绿。
T03 定向、现有问数和公共契约回归及全量检查；整轮交付仍等待 T04～T06。

## T04／T05 协议冻结（2026-11-16）

用户授权 T04 RED 后直接进入 T05。
同步响应和 SSE done/error 增量加入 `application_check`，公开应用／契约版本、准备／执行状态、缺项与建议、依据摘要及影子比较；不返回完整 IR、连接路径或私有异常。
环境变量 `DATAMIND_DEEPQA_CONTRACT_MODE` 支持 shadow、enforce、off；影子比较原 scope_error 与新准备结论后默认 enforce。任何模式均不允许已删除 key 或跨领域资产绕过实际 SQL 守卫。
缓存命中之前先做共享预检，键包含完整有效内容、有序多图、问题／最近历史、focus、契约版本、真实连接 schema 与生命周期记录；命中后逐条复核缓存 SQL，不能将存量报告当授权。
规划期间或逐条 SQL 之间依据变化返回 `stale_result`；同步用结构化错误，SSE error 结束。实际 SQL 受理与旧删除接口共享锁，保留单语句、只读、本体／列／连接口径的最终校验。
现有 build/delete 及 forged/delete 在删文件前持久记录 key 和修订，未来未知 key 仍沿用旧 fallback；已删除 key 即使复制旧文件或进程缓存丢失也拒绝。
该删除记录仅为已有旧接口提供问数拒绝依据，不提供 IR-029 的资产删除／恢复能力。
HTTP 仍以既有 graphs/tables 请求为主；asset_refs 不作为可忽略字段使用，跨领域引用拒绝，尚不提供从 HTTP 直问任意固定资产的新流程。T03 的固定资产只读核心接口继续保留。

T05 具体接点：实际宿主显式注入数据适配器，通用独立 registry 缺宿主时仍 unavailable；公共固定数据版本准备检查沿用已有持久化协议，不依赖机器人开关。
自动问数的原始 IR、SQL 和模型行为不因检查报告被客户端声明而改变；会话保存增量保留紧凑检查摘要。
没有选择图谱、默认 demo 及有记录 fallback 沿用旧准备豁免，仍逐条执行 SQL 安全／范围检查；影子比较验证其边界，不给普通问题增加指标认证。
