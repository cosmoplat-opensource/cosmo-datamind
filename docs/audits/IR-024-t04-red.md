# IR-024 T04 RED — TBox/ABox 核心测试

- 日期：2026-11-10（Asia/Shanghai）。
- 授权：用户在 T03 完成后要求“继续下一步”，按 IR-024 顺序进入 T04 RED。
- 状态：测试已创建并验证预期失败，待覆盖人审；未进入 T05 GREEN。
- 依据：[DR-070](../../specs/decisions/DR-070-robotics-dual-build.md)、[IR-024](../../specs/iterations/IR-024-robotics-dual-build.md)。
- 测试接口：[建模核心 v1](../../specs/contracts/robotics-modeling-core-v1.md)。
- 机器记录：[结果与测试摘要](IR-024-t04-red-results.json)。

## 覆盖内容

| 测试文件 | 用例数 | 主要断言 |
|---|---:|---|
| `test_robotics_modeling_evidence.py` | 10 | 两份真实 XML 的 25 次调用／129 个绑定独立核对；证据确定性、角色摘要、重复节点位置、XML 安全、词表固定身份与副本隔离 |
| `test_robotics_tbox_design.py` | 39 | 类别及能力问题确认、消息不能确认、词表与引用校验、无环层次、关系方向及完整并集、属性类型、空设计／过期确认拒绝、返回类别、失败保留旧状态、一次格式修正上限、附件指令与实例注入拒绝 |
| `test_robotics_abox_design.py` | 21 | 不接收／调用模型、真实 UR 抽取确定性、调用身份、步骤与数据流、端口冲突／不支持结构定位、未映射项不补造、确认与来源摘要、实例断言语义校验 |
| `test_robotics_design_export.py` | 9 | RDF 并集而非交集、class_ids 不变成 domain／属性值／限制公理、类型化字面量及全部断言、来源核验、非法 namespace 拒绝、不伪造旧 UR 应用兼容 |

参数化展开后共 79 项；测试模块及夹具不导入来源工程，也不调用真实模型。
模型替身只实现 T03 route 的 request 边界，不替换解析器、校验器、抽取器或 RDF 导出器。
网络 tripwire 阻止核心意外连接外部服务；ABox 另禁止调用 BuildRoute.request。

## 测试材料与来源

复用仓库已有 `tests/fixtures/robotics/sources/` 的两份真实 XML，摘要与来源工程示例一致。
新增 `tests/fixtures/robotics/modeling/ur_selected_tbox.json`，仅取来源样例的 namespace 与三组已选 schema（27 类、28 对象属性、18 数据属性）。
来源摘要与裁剪说明见 [provenance.json](../../tests/fixtures/robotics/modeling/provenance.json)。
没有复制旧会话标识、用户消息、历史证据、模型配置、密钥或整份术语目录。
没有迁入生产实现、词表和映射；T05 才完成这些工作。
小词表与结构化模型回复明确标记为合成测试数据，不能代表真实模型质量。

## RED 结果

```bash
.venv/bin/python -m pytest \
  tests/unit/test_robotics_modeling_evidence.py \
  tests/unit/test_robotics_tbox_design.py \
  tests/unit/test_robotics_abox_design.py \
  tests/unit/test_robotics_design_export.py \
  --tb=short -ra --junitxml=.check-results/IR-024-t04-red/junit.xml
```

结果：78 failed、1 passed、0 error、0 skipped，退出码 1，用时 0.58 秒。
78 项失败均为 `ModuleNotFoundError: No module named 'domains.robotics.modeling'`，即待实现的包内核心缺失。
动态 import 放在用例执行中，测试收集完整，没有用 importorskip 或 xfail 隐藏缺失实现。
通过的独立用例直接解析真实 XML，核对 25 次调用／129 个参数绑定及裁剪夹具 SHA-256；不依赖待实现代码。
这证明 RED 原因明确，不证明其余断言已经在实现上运行过；T05 仍需逐项验证全部断言。
日志：[pytest.log](../../.check-results/IR-024-t04-red/pytest.log)、[JUnit](../../.check-results/IR-024-t04-red/junit.xml)。

## 回归及静态检查

T03 的 62 项分流／HTTP 定向回归全部通过，用时 4.34 秒，未改动原测试。
日志：[T03 回归](../../.check-results/IR-024-t04-red/t03-baseline.log)。
5 份新 Python 测试／夹具通过 ruff 和 mypy；生产代码及 T03 已审阅测试摘要保持不变。
实际验收引用、文档本地链接、唯一验收编号、JSON 与 diff 格式另做自检。
当前是预期 RED，不运行已知必失败的全量 GREEN 检查；T05 实现后仍需按流程执行完整回归。

## 未覆盖的后续工作

- T06/T07：数据库迁移、持久设计会话、请求幂等、并发、崩溃恢复、失败提交、资产保存与应用准入。
- T08/T09：统一构建页面中的对话、确认、差异、恢复及最终双路径浏览器闭环。
- T10：真实模型试运行与人工语义评阅。

T04 的阶段测试只验证纯状态转换，不把内存状态不变说成数据库事务安全。
来源引用合法只证明可追溯，不能自动证明模型解释符合机器人领域事实，仍需用户确认设计。
RBD-05/06/07 已映射到实际测试但保持 RED；RBD-04/12 部分核心测试新增 RED，其余未实施条目仍留计划映射。
本阶段不勾选整轮交付／验收，也不改变 HTTP 的 model_build_pending 状态。

## 人审接点

[sdd-workflow](../../.agents/skills/sdd-workflow/SKILL.md) 第⑤环节要求：“人 review 测试是否覆盖到位”。
因此先提交上述具体覆盖和失败证据供审阅；用户确认后再进入 T05，实现包内核心并让本组测试转绿。
