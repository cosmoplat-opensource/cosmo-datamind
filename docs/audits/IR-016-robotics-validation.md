# IR-016 · 基线与分批 RED/GREEN 审查记录 — DR-060 / DR-062 / DR-065

T09 实现及自动验证已完成：89 项机器人测试通过，实际三场景演示通过，各回归检查段通过；当前待最终交付人审。
本记录是第一阶段自动验证与交付审查证据；最终交付状态由用户确认。

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

## T04 发布契约 RED（历史记录，已确认）

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
依据 [项目 SDD skill](../../.agents/skills/sdd-workflow/SKILL.md) 第⑤步“人 review 测试是否覆盖到位”，本批测试已由用户明确回复“T04已确认”，随后进入 T05 实现。
T03 已通过的 23 项与 T04 新增的 16 项预期失败分开记录；此处保留 T04 当时的失败记录，后续结果见 T05。


## T05 GREEN 与全量回归

T04 已审的 16 项全部通过，合并首批共 39 项通过、0 失败、0 跳过；已审测试及辅助模块摘要保持不变。
实现 publisher、consumer 与共享 artifact_contract，保留原协议和可信规则摘要。
发布器核对原子参数的重复属性与 mapsToPort 端口值是否一致，只删除一致的重复表示，不修复矛盾数据。
共享语义读取支持构建草稿与 v2 发布图，从 hasStep 和实际绑定推导所属流程与数据生产者，兼容旧 agent 图中没有冗余关系的表示。
条件定义改为构建与校验共用；发布时校验内存图，序列化后经消费入口再次验证实际文件，通过后以不覆盖的原子方式创建目标。
消费侧校验协议、规则、条件、固定模板及符号数据流，摘要对应本次读取的字节快照。

额外兼容探针在临时目录复制经 T01 摘要验证的原 agent 两个模块，对本轮新发布 OWL 执行原 load/prepare_generation，结果通过。
另对已归一化的发布物分别破坏绑定、条件、步骤，三项均明确拒绝，避免被草稿重复属性错误掩盖。
上述探针不是现场机器人执行，也不是正式任务业务证据准入。

全量命令本次实际退出 0：

```bash
env PATH="/home/dyh/.local/opt/node-v22.20.0-linux-x64/bin:$PATH" .venv/bin/python scripts/check.py --suite all --report-dir .check-results/ir016-t05-all
```

静态/类型检查通过；单元 868 通过 / 5 可选上游跳过，覆盖率 87.2%（门槛 81%）。
HTTP 551 通过 / 8 可选上游跳过，10 轮变序通过；UI 64 项、UI 实操 50 项、构建页浏览器 10 项通过，另有 3 项真实模型检查因未配置而跳过。
机器记录与原 agent 兼容证据见 [T05 GREEN 结果](IR-016-t05-green-results.json)。
严格任务准入、作者草稿入口及演示 CLI 仍待后续任务，IR 保持 in-progress。

## T06 严格任务与作者草稿 RED（历史记录，已确认）

本批需要确认的是下面的行为预期，不要求逐行阅读测试代码。
新增 33 项参数化测试，0 通过、33 失败、0 收集错误、0 跳过，退出码 1。
全部因 `domains.robotics.generation` 尚未实现而失败，后续业务断言尚未执行；没有添加占位实现或跳过测试。
完整结果及文件摘要见 [T06 RED 记录](IR-016-t06-red-results.json)。

| 覆盖内容 | 预期行为 | 项数 |
|---|---|---:|
| 新构建的真实本体缺映射和适配依据 | 返回 needs_information，列出缺口，不生成候选，不改本体 | 1 |
| 分别缺映射/参数/确认、参数仍为占位符，以及证据缺失、过期、版本/实现/零件类型/槽位不匹配、无来源 | 任意一个缺口都阻断正式候选，保留具体原因 | 11 |
| 用户先未选槽位，随后补齐选择 | 先澄清，再报告仍缺业务证据；选择对象不等于确认知识 | 1 |
| 作者输入不完整配置 | 可保存带占位值的 draft；同样缺口在严格入口不生成候选 | 1 |
| 请求重排、跳过坐标转换或任意技能组合 | 返回 unsupported，不偷偷丢弃附加要求 | 3 |
| 未指明操作/对象/目标、同名歧义、对象未登记、未知任务类型 | 缺信息先澄清；未知操作不套用抓移放模板 | 8 |
| 结构化任务多字段、少字段或类型错误 | 返回 error，不允许把数值配置混入六字段任务协议 | 4 |
| 演示上下文用于真实模式，或真实上下文用于演示模式 | 返回 mode_mismatch，不写候选 | 2 |
| 完整演示任务成功 | 生成明确标记 DEMO 的候选，四树及固定步骤保持一致，记录六个参数来源；执行状态仍为 not_verified | 1 |
| 禁止读取源 XML 后调用直接编译和严格任务入口 | 两条路径仍可用，原输入摘要不变 | 1 |

拟定接口位于 generation 模块，供 T07 实现：

- `generate_task(context, task, mode=…, output_dir=…)`：输入已加载本体上下文与六字段任务，返回状态、问题及候选路径；只有 generated 才能写严格候选。
- `create_authoring_draft(context, config, output_dir=…)`：显式作者入口，可保存带未解决值的草稿；返回 draft_path，candidate_written=false，不冒充正式生成结果。

任务六字段是 operation、part、station、carrier、slot、unsupported_requests，与六个公开技能参数是两种不同协议。
本批直接接收结构化任务，不调用真实模型，也不新增平台聊天持久化。
逐类证据缺口与成功测试使用冻结演示夹具的独立副本；新构建真实缺知识测试使用实际新发布物。
从新构建加显式演示补充直到成功生成的完整链路仍由 T08 验证，不能用本批演示夹具成功代替。
来源独立测试拦截两种入口对 XML 文件的读取；演示脚本、重复运行隔离和完整报告属于 T08/T09。

```bash
.venv/bin/python -m pytest tests/unit/test_robotics_policy.py tests/unit/test_robotics_source_independence.py -v --tb=short
```

T05 全量回归退出 0 的记录保留；加入本批测试后工作树为预期 RED，不声明当前全量通过。
依据 [项目 SDD skill](../../.agents/skills/sdd-workflow/SKILL.md) 第⑤步“人 review 测试是否覆盖到位”，用户已确认本批覆盖及草稿边界，随后进入 T07 实现。


## T07 GREEN 与全量回归

用户已确认 T06 覆盖，并明确作者草稿不包含在线 XML 编辑闭环；缺知识回到本体维护后重新发布生成。
实现 grounding 与 generation，任务六字段校验、实体选择、Profile 参数和适配证据检查均迁入领域包。
缺选择/歧义返回 needs_clarification，缺知识返回 needs_information，不支持任务返回 unsupported，格式/模式/校验/存储失败返回 error；只有成功才写候选。
作者草稿是单独显式入口，允许未解决参数占位、标记 draft，candidate_written 始终为 false。
所有成功候选仍为 execution_readiness=not_verified；生成不调用模型、不回读来源 XML、不修改图或配置。
消费上下文新增图摘要；生成前复核快照，修改已加载图后不能沿用旧工件版本产生候选。
写 XML 时先完成校验再原子创建；额外探针验证同名工件不覆盖、加载快照修改被拒绝、草稿不能改固定参数。

T06 的 33 项已全部通过，含原有测试共 72 项；已审测试文件及辅助模块摘要均未改变。
T08 新测试加入前启动的全量检查实际退出 0：

```bash
env PATH="/home/dyh/.local/opt/node-v22.20.0-linux-x64/bin:$PATH" .venv/bin/python scripts/check.py --suite all --report-dir .check-results/ir016-t07-all
```

静态/类型检查通过，单元 901 通过 / 5 可选上游跳过，覆盖率 87.3%（门槛 81%）。
HTTP 551 通过 / 8 可选上游跳过，10 轮变序通过；UI 64 项、UI 实操 50 项、构建页浏览器 10 项通过，3 项真实模型检查因未配置而跳过。
结果见 [T07 GREEN 记录](IR-016-t07-green-results.json)，完整新构建演示补充与用户 CLI 仍由 T08/T09 完成。

## T08 完整链路与离线命令 RED（历史记录，已确认）

本批审查聚焦“用户实际运行离线命令，能否核对新链路是否完成”，不要求逐行看代码。
17 项参数化测试中，1 项存储保护通过，16 项因演示模块或 CLI 尚未实现而失败，0 收集错误、0 跳过，退出码 1。
失败仍处在缺少入口阶段，后续产物和语义断言须在 T09 实现后实际执行。
机器记录见 [T08 RED 结果](IR-016-t08-red-results.json)。

| 覆盖内容 | 预期行为 | 项数 |
|---|---|---:|
| 本轮 XML 构建＋显式演示补充＋发布＋严格生成 | 原图不变，技能/调用/绑定/条件不变；移走来源 XML 后仍生成，输出按补充参数对照并追溯摘要 | 1 |
| 补充资料来源不匹配、冒充 real、修改固定参数 | 拒绝补充，不修改输入图 | 3 |
| 来源与归档夹具保护 | 构建、补充和发布后，仓库冻结输入及演示补充文件字节不变 | 1 |
| 离线命令一次运行全部场景 | 成功、真实缺证据、契约拒绝三类结果准确；禁网络、原归档与预置 OWL 读取；支持从非工程目录启动 | 1 |
| 三个场景分别执行 | 每个 --case 独立可运行，只报告所选场景 | 3 |
| 注入意外放行、契约误接受、缺失工件、读文件异常、来源格式异常 | 汇总 FAIL 并返回非零；异常不能冒充预期拒绝，报告成功必须有实际产物 | 5 |
| 输入来源不存在 | 记录 error、返回非零，不能算作契约拒绝场景通过 | 1 |
| 重复运行 | 创建不同运行目录，旧产物及来源字节不变 | 1 |
| 导入领域包时保护既有存储 | 独立临时工作目录中的历史消息、编辑数据与数据库哨兵不被修改；本项已通过 | 1 |

演示补充资料是新增的 `tests/fixtures/robotics/demo_supplement.json`，仅表示合成的演示知识，不是真实证据。
它记录适用来源摘要、业务对象、公开参数和模拟依据；特意将公开阈值从 0.8 改为 0.72，便于核对补充资料确实参与生成。
拟定 `demo.apply_demo_supplement(graph, path)` 返回独立的图、补充文件摘要和变更清单；不能用旧 agent OWL 替换整张新图。
非法补充资料与原图保护先由接口测试检查，CLI 再串联实际构建、发布、加载与生成模块。

脚本约定 `scripts/demo_robotics.py` 提供可测试的 `main(argv)` 并返回退出码；用户默认命令不变：

```bash
.venv/bin/python scripts/demo_robotics.py --case all --output-dir .check-results/robotics-demo
```

每次运行在 output-dir 下创建唯一子目录，生成 summary.json 与 summary.md；终端显示绝对路径、场景状态和 PASS/FAIL。
summary 记录 run_dir、passed，以及每场景的 case、expected_status、actual_status、passed、issues、artifacts（绝对路径与 SHA-256）。
成功场景包含 published.owl、demo_candidate.xml、validation_report.json；报告记录源/模型/本体/补充摘要、补充变更与执行未验证状态。
两个负向场景必须有拒绝说明和验证报告，不生成候选 XML；按预期拒绝可以记 PASS，基础设施错误必须记 FAIL。
`--source` 支持显式输入覆盖，用于复现来源缺失；默认仍使用随工程提供的来源文件，不要求模型密钥或原归档目录。

这些新增测试加入后，当前工作树为预期 RED；T07 全量退出 0 的记录不代表新增测试已通过。
依据 [项目 SDD skill](../../.agents/skills/sdd-workflow/SKILL.md) 第⑤步“人 review 测试是否覆盖到位”，用户已确认本批覆盖，随后进入 T09，实现离线演示并完成第一阶段最终验证。


## T09 GREEN 与最终交付审查证据

日期：2026-11-03。用户已确认 T08 覆盖，已实现 demo.apply_demo_supplement、offline_demo 编排器与 scripts/demo_robotics.py。
补充资料先核对来源/模型摘要和业务对象，校验公开配置，再生成独立 demo 图及逐条增删记录；不覆盖原图，不更改模板结构。
实际输出经过消费加载、固定模板编译与独立候选结构对照，命令按实际状态、文件存在性、摘要与输入保护确定 PASS/FAIL。
无效协议只在独立副本上制造，来源错误和读文件异常不会算作预期拒绝通过。

已审的 T08 17 项全部通过，合计 89 项机器人测试通过，全部已审测试及 JSON 夹具摘要保持不变。
直接运行交付命令退出 0：

```bash
.venv/bin/python scripts/demo_robotics.py --case all --output-dir .check-results/robotics-demo
```

| 场景 | 实际状态 | 产物核对 |
|---|---|---|
| demo-success | generated，PASS | 本轮 published.owl、demo_candidate.xml 和验证报告存在，公开阈值 0.72，四树固定结构保持，执行未验证 |
| real-missing-evidence | needs_information，PASS | 报告列明未确认实体、映射及适配证据缺口；没有候选 XML |
| invalid-contract | rejected，PASS | unknown-v99 协议被拒绝；正常发布物保留，无候选 XML |

实际运行目录为 `.check-results/robotics-demo/20261103T015738Z-bg93d6z2/`。
[本次可读汇总](../../.check-results/robotics-demo/20261103T015738Z-bg93d6z2/summary.md)可直接打开；工件摘要、汇总内容和检查结果亦归档于 [T09 机器记录](IR-016-t09-green-results.json)。
用户说明见 [离线演示使用说明](../robotics-offline-demo.md)。

首次全量检查退出 1，单元为 917 通过 / 1 失败 / 5 跳过。
失败原因是测试运行期间 AI 更新了夹具目录的 demo_supplement.md 说明文件，输入保护用例正确检测到字节变化。
完成说明编辑、固定输入后重跑单元检查，未修改测试、JSON 夹具或实现来绕过断言。
原全量通过的 HTTP、变序、UI 检查保留结果，不重复执行或改写原退出码：

```bash
env PATH="/home/dyh/.local/opt/node-v22.20.0-linux-x64/bin:$PATH" .venv/bin/python scripts/check.py --suite all --report-dir .check-results/ir016-t09-all
env PATH="/home/dyh/.local/opt/node-v22.20.0-linux-x64/bin:$PATH" .venv/bin/python scripts/check.py --suite unit --report-dir .check-results/ir016-t09-fixed-unit
```

最终验证结果：

| 检查 | 结果 |
|---|---|
| lint / 类型 | 通过 |
| 单元补验 | 918 通过，5 项可选上游跳过，命令退出 0 |
| 覆盖率 | 86.8%，原门槛 81% |
| HTTP | 551 通过，8 项可选上游跳过 |
| 10 轮变序 | 通过 |
| UI 走查 | 64 项通过 |
| UI 实操 | 50 通过，3 项未配置真实模型而跳过 |
| 构建页浏览器 | 10 项通过 |

首次全量退出 1 和补验退出 0 分开记录，最终所有必需检查段通过。
第一阶段业务内核与离线演示已就绪；平台 UI/API、真实 LLM 提取、持久化运行服务、在线 XML 编辑与机器人执行不在本轮范围。
依据 [项目 SDD skill](../../.agents/skills/sdd-workflow/SKILL.md) 第⑨步“人确认这一轮做完了”，IR 保持 in-progress，等待用户最终验收后勾选清单并标记 delivered。
