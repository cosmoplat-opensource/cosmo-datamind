# IR-016 · 基线、T03 GREEN 与 T04 RED 审查记录 — DR-060 / DR-062 / DR-065

当前已完成 T01 基线、用户已审 T02 测试与 T03 实现；新增 T04 发布契约测试待审。
本记录不是第一阶段最终交付验收报告。

## 本轮执行结果

- T01：8 份夹具已冻结，9 个原项目探针匹配已登记基线；其中跨目录测试实际失败，详见 [基线记录](IR-016-robotics-baseline.md)。
- T02：pytest 收集 23 项，2 项通过、21 项失败、0 收集错误、0 跳过；退出码 1。
- 静态检查：新增审计脚本、测试辅助模块及四个测试文件的 ruff 检查通过。
- 最终全量回归尚未执行；当前预期 RED 不应被隐藏或写成全量通过。

机器可读结果见 [T02 RED 结果](IR-016-t02-red-results.json)。
本地完整 JUnit 输出为 `.check-results/ir016-t02-red.xml`，不将其临时路径作为长期审计唯一来源。

```bash
.venv/bin/python -m pytest tests/unit/test_robotics_baseline.py tests/unit/test_robotics_imports.py tests/unit/test_robotics_semantics.py tests/unit/test_robotics_compiler.py -v --tb=short --junitxml=.check-results/ir016-t02-red.xml
```

所有失败都来自尚不存在的 `domains.robotics` 包，具体错误是 `ModuleNotFoundError: No module named 'domains'`。
这证明测试已发现缺失的集成入口，不代表下游语义断言已经逐项执行；T03 包化后须继续执行全部断言并解决实际行为差异。
没有添加占位生产实现、xfail 或 skip，也没有更改既有业务代码。

## 人审覆盖清单

| 测试文件 | 已写的检查 | 本次状态 |
|---|---|---|
| test_robotics_baseline.py | 来源字节与摘要、原始结果记录、两侧 OWL 契约差异 | 2 通过 |
| test_robotics_imports.py | 两种导入顺序、临时 cwd、无 sys.path 改写、无旧模块别名、无导入期资产读取和网络 | 2 失败 |
| test_robotics_semantics.py | 25 次调用、3 次独立 MoveL、端口与绑定、作用域、来源往返、RDF 图同构、业务证据不自动确认；未知节点、重排、控制节点、模型冲突、空文件、非法 XML、缺模型和未知 profile | 9 失败 |
| test_robotics_compiler.py | 仅六处公开配置变化、内部树不变、输入不被修改；拒绝重排、固定值修改、非法数据引用、非法或额外参数 | 10 失败 |

23 项包含参数化展开，不能误称 23 个独立测试函数。
当前仅覆盖 T02 的结构层要求，发布契约、任务证据策略、多轮交互与演示 CLI 将在 T04、T06、T08 分别补充。
ROBOTICS 用户权限和运行并发不属于当前纯函数包化任务，由后续 API/会话阶段验证。

## 测试提出的最小接口

这些接口是供 T03 实现的可审查边界，不是已经存在的 API。

| 模块与接口 | 契约 |
|---|---|
| importer.build_graph(source, models, profile="ur_pick_place") | 返回 rdflib.Graph，接受显式文件输入；不支持的 profile/结构/模型报 ValueError，文件缺失可报 FileNotFoundError，非法 XML 可报 ParseError |
| semantics.xml_from_graph(graph) | 从 RDF 恢复四棵树的执行结构，不输出 TreeNodesModel |
| semantics.node_models_from_graph(graph) | 从图中的端口契约恢复内存节点模型 |
| semantics.configuration_paths(graph) | 根据图中绑定生成公开配置位置，避免依赖外部源 XML |
| compiler.instantiate(template, config, models_root=…, config_paths=…, baseline=…) | 返回新的 XML 根节点，仅修改允许配置；非法值或模板漂移报 ValueError |
| validator.validate_tree(root, models, baseline=…, config_paths=…) | 返回问题列表；合法树返回空列表，不把错误吞掉 |

这里的 instantiate 是结构性实例化原语，不是经过业务证据准入的正式任务生成入口。
它允许测试传入配置以验证编译规则；未来面向任务的入口必须先进行实体、映射和证据检查，不能将此原语直接开放给聊天模型。
测试进程通过 PYTHONPATH 指向本仓库，产品模块本身不得改写 sys.path 或读取原归档目录。
同一技能的重复调用、源 XML 中的真实属性和预定六处参数变化由独立断言比较，不能只靠“能生成 XML”判定通过。

## 当前流程位置

用户已要求按 SDD 继续下一步，本轮据 IR 执行 T01 和 T02。
依据 [项目 SDD skill](../../.agents/skills/sdd-workflow/SKILL.md) 第⑤步“人 review 测试是否覆盖到位”，上述测试已由用户明确确认，随后进入 T03。
审查后若需要补充或调整测试，应在进入 T03 之前记录修改原因；进入 GREEN 后按 skill 只修改实现，不为变绿削弱已审测试。
IR 的最终交付与验收框尚未勾选，DR 状态不自动提升为 accepted。

## T03 GREEN 与回归

首批 23 项已全部通过，未修改 T02 已审测试，文件摘要见 [机器可读记录](IR-016-t03-green-results.json)。
新增显式领域包，迁入原构建侧算法并拆分职责；固定四棵树的结构限制同时用于来源验证与编译校验。
构建保留 25 次调用及其绑定；编译从 RDF 推导公开配置位置，严格比较基线，非法数值或模板修改被拒绝。
当前 compiler 是低层结构性原语，正式任务的实体映射与证据准入仍属于后续任务。

首次 `.venv/bin/python scripts/check.py --suite all` 退出 1，原因及修复如下：

| 问题 | 处理 |
|---|---|
| 安装的 SDD 模板中 os 未使用 | 删除闲置导入，未改已审测试 |
| 未来验收项 tests 为空，不满足现有追溯检查 | 保留全部要求，将未落地条目移到 planned_requirements，不填虚假测试或放宽检查 |
| node 未在 PATH | 使用本机已有 Node v22.20.0，仅为检查进程补充 PATH |

失败检查段分别重跑，原全量运行中已经通过的 UI 段保留结果，不伪造原退出码：

```bash
env PATH="/home/dyh/.local/opt/node-v22.20.0-linux-x64/bin:$PATH" .venv/bin/python scripts/check.py --suite unit --report-dir .check-results/ir016-t03-covered-unit
env PATH="/home/dyh/.local/opt/node-v22.20.0-linux-x64/bin:$PATH" .venv/bin/python scripts/check.py --suite integration --report-dir .check-results/ir016-t03-fixed-integration
```

最终静态/类型检查通过，单元 852 通过 / 5 可选上游跳过，覆盖率 87.1%（含机器人模块，门槛仍为 81%）。
HTTP 551 通过 / 8 可选上游跳过，10 轮变序通过，UI 64 通过，UI 实操 50 通过 / 3 真实模型跳过，构建页浏览器 10 项通过。
本轮未验证真实模型或现场机器人，发布器和离线演示命令也尚未实现，不能据此宣布 IR-016 整体 delivered。

## T04 发布契约 RED（本次待审）

新增 `tests/unit/test_robotics_publication.py`，参数化展开共 16 项，0 通过、16 失败、0 收集错误、0 跳过，退出码 1。
全部失败来自尚不存在的 publisher 或 consumer 模块，具体断言需在 T05 实现后执行；没有新增占位实现或跳过用例。
机器可读记录见 [T04 RED 结果](IR-016-t04-red-results.json)。

```bash
.venv/bin/python -m pytest tests/unit/test_robotics_publication.py -v --tb=short
```

| 覆盖内容 | 项数 |
|---|---:|
| 新 XML 构建图发布后加载：摘要、规则校验、四树结构、25 次调用和未确认业务映射保留 | 1 |
| 既有 agent real/demo OWL 自包含加载，忽略外部 sidecar，demo 不进入 real | 2 |
| 缺失/未知/重复协议、缺失/篡改规则、外部 imports 均拒绝 | 6 |
| 错误绑定、未知条件规则、重复步骤号：发布和消费均拒绝，仅补正确元数据不能绕过 | 3 |
| DTD/实体在 RDF 解析之前拒绝 | 2 |
| 发布不覆盖已有文件，非 OWL 输入在读取前拒绝 | 2 |

接口约定：`publisher.publish_ontology(graph, target, mode="real")` 验证后写入新文件，保持输入图不变；`consumer.load_ontology(path, mode="real")` 返回包含 graph 与 validation 的上下文。
本阶段保留已冻结 agent 的 `ur-pick-place-owl-v2` 协议及其内嵌规则摘要，不自行升级协议或放宽可信规则。
兼容性检查使用真实 agent OWL 夹具和迁入的消费入口；不声称本批测试执行了原项目 agent 进程。
这些测试验证发布和加载边界，严格任务的缺证据拒绝及完整离线演示仍分别属于 T06 和 T08。

用户此前的“测试覆盖已确认”用于 T02，本批 T04 是新的测试审查范围。
依据 [项目 SDD skill](../../.agents/skills/sdd-workflow/SKILL.md) 第⑤步“人 review 测试是否覆盖到位”，本批测试提交后等待人审，再进入 T05 实现。
T03 已通过的 23 项与 T04 新增的 16 项预期失败分开记录；当前工作树不能标记为全绿。
