# Schema 2 冻结迁移夹具 — DR-059 / DR-064 / DR-065

用于 IR-018 T02，验证旧数据真实迁移，不用新版初始化器伪造旧库。

- 冻结来源提交：`05a6df947568e0645a37c25944003f7dc0e7f017`。
- 文件：`schema2_snapshot.json`，SHA-256：`b97203848dddd87c306bd287fd9cfe97d7a2f68c0172a6df03af96220cd44b39`。
- 来源：在临时目录运行 IR-017 原有 AssetStore / RunStore 服务，读取 SQLite 的真实 schema、行与实际磁盘文件，序列化成可审查 JSON；文件内容使用 base64 保存。
- 包含数据资产及来源/版本、legacy_assets 绑定、queued/running/completed/failed 运行、retry_of、事件、报告和 manifest。
- 包含从冻结双 XML 新构建、经消费验证且显式发布的真实模式机器人 OWL；实际运行结果为 needs_information，未补 demo 证据。
- 另一个 inspect/generated 数据运行使用 tests.run_support 的合成 XML，只验证历史存储兼容，不代表可执行机器人行为树。
- 测试通过 tests.build_pipeline_support.restore_v2 直接还原旧表与文件，并独立检查摘要/外键；随后才调用待测的新初始化器。
- 此夹具在本轮测试覆盖确认后冻结，不能在 T03 用新实现重生成来规避迁移失败。
- 所有数据来自仓库测试输入或合成数据，不复制用户工作目录、聊天、凭据或业务数据库。

## Schema 5 冻结夹具 — DR-070 / IR-024 T06

`schema5_snapshot.json` 用于设计会话迁移测试，由 T07 实现前的代码在临时目录还原 schema4，再实际升级到 schema5，并创建一个合成离线策略任务会话后冻结。
它包含 16 张表、8 条旧运行、22 件字节文件，以及旧数据资产、机器人资产、来源构建、manifest 和任务会话。
`provenance` 记录 schema4 夹具与当时 storage.py 摘要；`base_created_from_commit` 仅指继承的 schema4 来源提交，不声称是本次快照的代码提交。
测试通过 `tests.robotics_design_support.restore_v5` 直接恢复 SQL、行与字节，再调用待测初始化器，不能在 T07 用新 schema 反造旧库。
没有读取用户数据库、聊天或模型配置，没有调用真实模型。
文件摘要记录在 `docs/audits/IR-024-t06-red-results.json`；人审后冻结该文件及新测试。
