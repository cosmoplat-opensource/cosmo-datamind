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
