# IR-024 T01 契约冻结与 T02 RED 记录 — DR-070

- 日期：2026-11-10（Asia/Shanghai）。
- 用户已确认方案无问题并要求继续下一步；DR-070 记为 accepted，IR-024 记为 in-progress。
- 当前结论：T01 完成；T02 预期失败已复现，等待测试人审；未进入 T03 实现。

## 冻结内容

- [双路径构建契约 v1](../../specs/contracts/robotics-dual-build-v1.md)：配置判定、路由快照、单模型调用边界、共同 XML 准入、HTTP 状态及后续设计接口。
- [来源摘要](../../specs/sources/IR-024-yang-sources.json)：两个用户提供工程的 17 份核心代码／词表／样例／参考测试 SHA-256；未迁入其生产代码或配置。
- [路由测试](../../tests/unit/test_robotics_dual_build_routing.py)：无模型、有效配置、残缺／错误／残留配置、多运行时优先序、环境优先、模型错误、身份固定和恢复。
- [HTTP 与旧路径测试](../../tests/unit/test_robotics_dual_build_http.py)：双 XML、提前拒绝不合法 XML、禁止客户端指定方法、功能开关、方法标识及真实旧链路。
- [合成运行时边界](../../tests/robotics_dual_build_support.py)：仅替换模型配置、注册和调用；所有实际网络连接被测试夹具阻止。

## RED 实测

命令：

```bash
.venv/bin/python -m pytest \
  tests/unit/test_robotics_dual_build_routing.py \
  tests/unit/test_robotics_dual_build_http.py \
  -ra --tb=short --junitxml=.check-results/IR-024-t02-red/junit.xml
```

结果：62 项收集成功，51 failed、11 passed，0 error、0 skipped，退出码 1，pytest 报告用时 5.00 秒。
没有使用 skip、xfail 或占位 assert 制造结果。
新模块导入放在测试执行阶段，因此模块缺失表现为单项失败，不会阻止 HTTP 和真实旧链路验证。

| 预期失败原因 | 数量 | 下一步需要实现的行为 |
|---|---:|---|
| `domain_runs.robotics_build_routing` 尚不存在 | 29 | 配置解析、单路由选择、公开快照及模型失败边界 |
| 不合法 XML 仍被旧接口以 202 接收并入队 | 20 | 在共同准入层先拒绝解析错误、DTD／实体、节点数和深度超限，且不登记来源 |
| 残缺模型配置仍被当作普通离线构建接收 | 1 | 配置错误与未配置分开，返回 configuration_error |
| 旧响应没有明确的 method/model | 1 | 保持旧响应语义并增加离线方法标识 |

已通过的 11 项分别为缺少 XML 角色 4 项、拒绝客户端指定模型／方法 5 项、功能关闭 1 项、真实旧流程 1 项。
真实旧流程执行原 importer/publisher/consumer、资产提交、流程和来源摘要核对、25 个技能调用身份检查、作者草稿生成及 OWL HTTP 下载字节核对。
作者草稿仍是 draft，未被声明为正式候选或实机执行就绪。

## 基线与静态检查

```bash
.venv/bin/python -m pytest \
  tests/meta/test_acceptance_traceability.py \
  tests/unit/test_robotics_build_http.py \
  tests/unit/test_robotics_build_pipeline.py \
  -ra --tb=short --junitxml=.check-results/IR-024-t02-red/baseline-junit.xml
```

结果：65 passed，退出码 0，用时 34.08 秒。
新增三个 Python 文件的 ruff 与 mypy（follow-imports=skip）均通过。
验收映射函数存在性、全局 requirement ID 唯一性和 14 个规约链接检查通过；git diff --check 通过。
现有 75 条正式映射不变；新增 5 条指向实际测试的映射，7 条仍在 planned_requirements。
RBD-04 与 RBD-12 明确为部分测试覆盖；持久化保留旧修订、格式修正预算、附件指令隔离和跨功能回归仍需后续任务验证。

## 边界与下一步

本次未修改生产实现，未调用真实模型，未读写用户现有机器人资产，未迁入外部工程的 .env、sessions 或 generated。
不运行整套 `check.py --suite all` 冒充全绿，因为新增行为测试此阶段应保持 RED。
上述合成模型用例不验证模型的建模质量，也不表示新模型构建页面已经可用。

按 [sdd-workflow 第⑤环节](../../.agents/skills/sdd-workflow/SKILL.md) 的“人 review 测试是否覆盖到位”，先审阅本组测试，再进入 T03 GREEN。
T03 只实现已冻结路由与共同准入／旧路径接点，不提前实现后续未审的 TBox/ABox 状态机。

原始记录保存在 `.check-results/IR-024-t02-red/pytest.log`、`junit.xml`、`baseline.log` 和 `baseline-junit.xml`。
这些是临时隔离验证记录，不属于本体运行数据。
