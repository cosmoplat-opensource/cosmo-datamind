# IR-024 T06 RED — 设计会话、迁移与候选应用边界

> 历史记录：用户随后要求统一总 JSON，当前契约与测试结果见 [评审修订](IR-024-t06-unified-json.md)。以下保留首次 RED 的 83 项结果。

- 日期：2026-11-10（Asia/Shanghai）。
- 授权：用户要求“继续下一步”，按已确认 IR-024 的 T06 编写失败测试。
- 状态：RED 已验证，待测试覆盖人审；没有进入 T07 GREEN。
- 依据：[DR-070](../../specs/decisions/DR-070-robotics-dual-build.md)、[IR-024](../../specs/iterations/IR-024-robotics-dual-build.md)。
- 接口：[设计服务测试接口 v1](../../specs/contracts/robotics-design-service-v1.md)。
- 机器记录：[结果、失败原因及文件摘要](IR-024-t06-red-results.json)。

## 覆盖内容

| 测试文件 | 展开用例数 | 核心断言 |
|---|---:|---|
| `test_robotics_design_service.py` | 32 | 无描述时只保存来源、统一 BuildService 接点、分轮排队、固定模型、请求重放与冲突、严格修订号、阶段／快照确认、失败保留结果、配置变化与密钥轮换、显式重试、会话隔离、密钥不持久化 |
| `test_robotics_design_storage.py` | 14 | 冻结 schema5、显式迁移、提交回滚、并行升级、只读不迁移、SQL 输入 CHECK、独立进程读取、恢复中断、旧运行不被误中断、历史副本与工件损坏、禁止绕过设计完成契约 |
| `test_robotics_design_concurrency.py` | 7 | 相同提交只登记一次、同修订两写者竞争、确认与消息竞争、原子领取、队列容量、模型等待不占数据库／RDF 写锁、恢复后迟到响应不覆盖重试 |
| `test_robotics_design_failures.py` | 15 | 两来源写入失败、TBox 工件写失败、ABox 版本／工件写失败、SQLite 提交失败、模型前后／ABox 前来源损坏、不支持 XML 的定位报告、不登记假成功、恢复后显式重试 |
| `test_robotics_design_assets.py` | 10 | 确认后独立确定性抽取运行、候选详情／通用图谱／固定下载、manifest 与实际输出一致、只读无副作用、编辑后旧版本保留、新版本谱系、旧完成契约继续拒绝通用设计、损坏／串修订检测、部分覆盖报告不丢失 |
| `test_robotics_design_compatibility.py` | 5 | 真实旧 UR 与新设计的资格区分、任务服务和 HTTP 防绕过、伪造 supported 标签无效、旧详情与下载链接、旧离线任务策略兼容 |

总计 83 项；新增共享材料在 [robotics_design_support.py](../../tests/robotics_design_support.py)。
服务测试直接调用真实持久化接点，未来实现必须使用真实 SQLite、来源存储、T05 校验／抽取／导出和资产版本提交。
模型边界使用合成 runtime.run_turn 回复，并经真实 T03 配置解析与调用适配器；不替换解析器、数据库或完成结果。
网络 tripwire 阻止意外真实模型请求。
补充的夹具诊断已用 T05 核心验证完整及缺少 hasBinding 两种合成提议确实可通过本地阶段校验、到达真实抽取，后者生成缺口报告。
该诊断仅验证测试材料，不表示尚未实现的服务断言已经通过。

## 冻结旧库

新增 [schema5_snapshot.json](../../tests/fixtures/domain_storage/schema5_snapshot.json)，包含 16 张表、8 条旧运行、22 件字节文件和旧任务会话。
生成步骤：在临时目录直接恢复既有 schema4 夹具，使用 T07 前的 Storage 实际迁移到 schema5，再用旧 TaskService 创建合成任务会话；冻结真实 SQL、数据库行和文件字节。
没有读取用户工作目录中的数据库、模型设置、聊天或密钥，没有调用真实模型。
`provenance` 记录基底夹具及旧 storage.py 摘要；继承的提交编号明确标为 base_created_from_commit。
迁移测试直接重放冻结内容，先独立确认 schema5 及旧 CHECK 确实拒绝 design_tbox，再调用待测初始化器。
人审后该夹具与新测试一起冻结，不能在 T07 用新 schema 重新生成来掩盖迁移错误。

## RED 实际结果

```bash
.venv/bin/python -m pytest \
  tests/unit/test_robotics_design_{service,storage,concurrency,failures,assets,compatibility}.py \
  --tb=short -ra --junitxml=.check-results/IR-024-t06-red/junit.xml
```

最终结果：79 failed、4 passed、0 error、0 skipped，退出码 1，用时 6.65 秒。
失败分布：74 项缺少 `domain_runs.robotics_design_service`；3 项 schema5 尚未升级；2 项只读构造尚未拒绝需要升级的 schema5。
这是当前实现缺失的预期 RED，不是业务已通过后又故意破坏实现。
缺失模块在测试执行阶段导入，没有收集错误，也没有 importorskip／xfail。
多数故障注入与后续断言尚未走到实际执行位置；T07 必须逐项跑通，当前不能声称事务回滚、并发恢复或新资产详情已经验证成功。

通过的四项独立基线为：

1. 冻结 schema5 的真实约束、历史行和字节一致性。
2. 原 build_completion 拒绝新核心导出的通用设计 OWL。
3. 原任务 HTTP 拒绝带伪造兼容元数据的通用设计，且不留下任务／运行。
4. 旧任务服务仍能以 offline 策略打开真实旧 UR 候选。

完整日志：[pytest.log](../../.check-results/IR-024-t06-red/pytest.log)、[JUnit](../../.check-results/IR-024-t06-red/junit.xml)。

## 回归与追溯

既有人审的 62 项 T03 分流／HTTP 测试和 79 项 T05 核心测试共 141 项全部通过，用时 6.29 秒。
日志：[baseline.log](../../.check-results/IR-024-t06-red/baseline.log)。
7 个新增 Python 文件通过 ruff 与 mypy，验收引用自检通过。
87 个验收编号无重复，732 条实际测试引用均有对应函数；本组每个新测试均已映射到至少一条验收要求。
生产文件、T02/T04 已审阅测试及两项已获准的旧测试调整的摘要均与前阶段审计一致。
未修改生产代码，未修改已审阅测试，未提交 git commit，也未重启用户服务。
当前预期 RED，没有运行已知必红的完整 GREEN 回归；T07 实现后仍需执行 scripts/check.py --suite all。

RBD-08/09/10 从 planned_requirements 移入真实映射，但状态为 RED。
RBD-03/04/05/06/12 保留之前通过证据，并明确新加入的 T06 部分仍为 RED。
RBD-11 的 UI 验收继续保留计划状态。
交付／验收复选框保持未勾选，IR 保持 in-progress。

## 边界与人审接点

本阶段没有新增生产 LLM 调用；测试的模型回复是合成数据，不代表真实端点连通性或自然语言建模质量。
现有 T05 已能经平台模型适配器执行建模核心；公开上传入口仍未完成会话与 UI 闭环。
完整 HTTP 请求体、权限与开关、目录／应用页资格展示及真实浏览器流程继续属于 T08/T09；真实模型试运行属于 T10。
已有两个 HTTP 基线检查仅保护旧任务防绕过与旧资产链接，不代表完成 T08。

[sdd-workflow](../../.agents/skills/sdd-workflow/SKILL.md) 第⑤环节明确要求：“人 review 测试是否覆盖到位”。
因此本阶段在具体测试、接口与 RED 证据完成后停留于人审接点；用户确认后进入 T07，仅修改实现让这些测试转绿。
