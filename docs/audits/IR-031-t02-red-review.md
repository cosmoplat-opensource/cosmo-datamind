# IR-031 T02 · 投影与共享控制器 RED 人审 — DR-159

日期：2026-11-17。
分支：`feat/studio-ui-integration`；T01 提交 `4905b5f`。
依据：[视图契约 v1](../../specs/contracts/studio-graph-view-v1.md)。

## 测试覆盖与独立依据

- 纯投影：类别／属性／实例阶段、退回类别、不伪造等待进度、空结果、确认和摘要不符、完整 IRI、空 RDF、旧本体、固定版本损坏与规模拒绝。
- 手算 RDF：Arm 有两个显式实例、Gripper 一个、Resource 零个；共享实例去重，不从继承推理数量；并集定义域与两个独立 domain 含义分别保留。
- 值与身份：空字符串、integer 0、union 匿名节点重解析稳定、不同命名空间的同名类别与实例不混淆。
- 持久化只读性：Harness 生成真实固定候选，在投影前后比对数据库行、文件字节和模型调用次数。
- 共享控制器：同来源更新保留视图，旧 epoch／revision 拒绝，删除清理选择，50 条分批、共享归属、暂停／固定、隔离 SVG ID、销毁动画与观察器。

测试主体见 [投影用例](../../tests/unit/test_studio_robotics_projection.py)、[pytest 组件入口](../../tests/unit/test_studio_graph_ui.py)、[Node 组件行为](../../tests/frontend/studio-graph-ui.mjs)。
Node 的 Element 小模型只承载控制器／GraphView 状态测试，不模拟浏览器排版和真实渲染性能。
鼠标拖拽邻居移动、移动端几何、页面 HTTP 绑定、资产删除策略、真实浏览器加载和性能均属于 T04／T05，不在此处宣称覆盖完成。

## 实际结果

`pytest tests/unit/test_studio_robotics_projection.py tests/unit/test_studio_graph_ui.py -q --tb=short -o addopts= --junitxml=...`：29 failed、2 passed、0 errors、0 skipped。
新增投影及控制器尚不存在；测试通过显式断言报告缺失产品能力，收集正常，Node 和 Python 环境可用。
所有 RED 目前止于这些能力缺失前置断言，后续语义／状态断言是待 GREEN 验证的固定预期，不能将它们当作已经逐个复现的行为错误。
两个通过项独立核对手算 RDF 与设计确认摘要夹具，没有把夹具通过误记成产品 GREEN。
未加入 skip／xfail，也未更改现有测试断言或业务代码。
机器明细见 [T02 results](IR-031-t02-results.json)。

相关回归：`tests/meta/test_acceptance_traceability.py`、`test_robotics_graph_views.py`、`test_robotics_design_assets.py`、`test_robotics_design_ui.py` 共 44 passed。
Node 24.18.1 使用 Playwright 自带运行时，旧 Node 测试只在测试子进程 PATH 中可见；不改系统安装。
新增 Python 测试 ruff、Node harness 语法、`git diff --check` 通过。
本次使用 `-o addopts=` 关闭默认极简输出以保存准确统计，不删除或替换测试项；完整回归仍按 T06 的标准脚本执行。

## 追溯与下一步

STV-R01／02／04／05／06 登记实际存在的测试路径，status=red，保留原 browser 计划和未完成覆盖说明。
STV-R03 继续保留在 planned_requirements，避免以 Node 状态测试冒充真实拖拽验证。
按 sdd-workflow 第⑤环节“人 review 测试是否覆盖到位”，本次提交 T02 供人审，下一任务为 T03 GREEN。
评审重点是显式实例口径、并集含义、历史／当前隔离、展开分批以及真实浏览器留给 T04 的边界是否符合需求。
T03 只能实现这些既定行为；不得通过弱化断言、跳过或删除用例获得 GREEN。
