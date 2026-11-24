# IR-040 · 公开验证记录的材料边界

状态：delivered。关联 [DR-174](../decisions/DR-174-public-verification-records.md) 和 [SPEC-MATERIAL-01](../acceptance.json)。

## 验收

- 公开决策、迭代与索引将历史结果归到对应版本和验证范围。图谱原交付的全量记录与后续命名修订的定向回归分别标明。资产生命周期的最终全量记录对应 2026-11-17 的交付范围。
- 公开复现使用仓库内脚本、合成数据和隔离存储。演示命令说明如何在读者自己的目录生成结果；原始记录仅在内部留存的检查保留其验证边界。即使只检出合并后的公开版本，读者也能运行仓库中的检查。
- 交付项描述产品行为及验收范围。单机服务操作、已有任务回读、开发者的 Git 进度和单次输出路径留在内部记录。运维命令、事务提交和失败重试等产品说明按实际用途保留。

## 复现

在[项目测试环境](../../README.md#测试)中运行 `python -m pytest -q tests/meta` 检查材料引用、公开复现入口与验收映射。阅读历史结论时，对照 DR、IR 和[规约索引](../map.md)中的日期、实现版本与测试范围。

| 读者要复核的行为 | 仓库入口 |
|---|---|
| 离线演示的三个预期结果及输出位置 | [IR-016](IR-016-robotics-contract-integration.md) |
| 详情、固定版本读取与删除恢复 | [IR-027](IR-027-ontology-detail-experience.md)、[IR-029](IR-029-ontology-asset-lifecycle.md) |
| 生命周期与问数合并后的兼容性 | [IR-030](IR-030-master-development-integration.md) |
| 只读图谱、任务配置切换与显式重试 | [IR-031](IR-031-robotics-interactive-ontology-graph-visualization.md)、[IR-036](IR-036-task-configuration-validation-scope.md) |

上述入口用于在所检出版本重新验证软件行为。内部资产、真实模型和现场设备的历史运行超出这些公开用例的复现范围。
