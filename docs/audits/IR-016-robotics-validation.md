# IR-016 · T01 基线与 T02 首轮 RED 审查记录 — DR-060 / DR-062 / DR-065

当前已完成基线取证和首轮失败测试编写，尚未进入 T03 实现。
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
依据 [项目 SDD skill](../../.agents/skills/sdd-workflow/SKILL.md) 第⑤步“人 review 测试是否覆盖到位”，现提交上述测试及接口供人审。
审查后若需要补充或调整测试，应在进入 T03 之前记录修改原因；进入 GREEN 后按 skill 只修改实现，不为变绿削弱已审测试。
IR 的最终交付与验收框尚未勾选，DR 状态不自动提升为 accepted。
