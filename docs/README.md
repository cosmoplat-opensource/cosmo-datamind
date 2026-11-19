# COSMO DataMind 文档

[项目首页](../README.md) · [项目获奖与应用案例](awards/README.md)

## 试用与部署

| 要做的事 | 从这里开始 |
| --- | --- |
| 打开制造运营演示库，查看已建好的本体 | [首页启动命令](../README.md#在本机运行) · [演示库、图谱与历史记录说明](../examples/README-demo.md) |
| 从六张 SQLite 表建本体，核对月度毛利率 | [快速入门](../QUICKSTART.md) · [示例 SQL](../examples/sample_db.sql) |
| 用业务文档辅助建模、给对象加中文别名、用自然语言问数 | [模型配置与问数教程](TUTORIAL-llm-build-qa.md) |
| 配置数据源、工作目录、服务端口和反向代理 | [安装部署](../INSTALL.md) · [配置示例](../.env.example) · [安全说明](../SECURITY.md) |

工作台默认供单机、单用户使用。切换侧栏的“当前数据库”可查看对应库的数据目录、质量检查和图谱。新库可先预览表或运行只读 SQL，再为该库构建本体。保存的看板按图表保留各自的数据来源。

## 数据建模与问数

| 主题 | 功能与依据 |
| --- | --- |
| 对象与关系 | 从表结构、声明外键和取值统计中提取证据。关系记录区分数据验证（`verified`）、人工确认（`asserted`）和候选（`candidate`）等状态。可检查连接键、父键唯一性、取值重叠及候选被保留的原因。[构建流程](pipelines/ontology_build.yaml) · [人工复核](../specs/decisions/DR-013-human-review-relations.md) |
| 业务术语与公式 | 构建时可以分别填写业务问题与验收问题。业务问题用于发现术语、派生公式和取数需求，验收问题用于检查本体能否回答预定问题。可在业务语义复核中确认或否决字段绑定。[业务概念与数据目录](../specs/decisions/DR-093-research-semantic-capabilities.md) |
| 行业与标准 | 可选用制造、电子制造和能碳建模技能，结合本地 BFO、IOF、gUFO 或 B2MML 资源做候选映射。参照模式将缺项列入人工复核，强约束模式会将核心缺项计入验收结论。[构建技能](../skills_seed/) · [标准资源与许可](../ontology/standards/README.md) · [参照模式](../specs/decisions/DR-052-build-industry-and-standard-references.md) |
| 指标计算 | 记录指标的聚合方式、过滤条件、时间和维度。运行编译后的 SQL，与参考值或来源查询比对，再由业务人员确认指标。可追溯计算口径和验证记录。[指标定义与生命周期](../specs/decisions/DR-054-metric-contract-and-lifecycle.md) · [参考值的核对顺序](../specs/decisions/DR-099-evidence-strength.md) |
| 问数与评测 | 查看规划阶段选入的表、SQL 实际使用的表和 JOIN。评测可用同一组问题对比不同检索方式，也可在本体修改前后成对回放历史问答。[问数流程](pipelines/deep_qa.yaml) · [SQL 检查与评测口径](../specs/decisions/DR-134-check-api-and-evaluation-statistics.md) · [反馈回放](../specs/decisions/DR-140-cancel-and-paired-replay.md) |
| 本体维护与动作 | 检查数据源漂移和本体兼容性，管理本体版本、回收和恢复。动作可登记、审批并跟踪执行回执，问数中的试算使用单独配置的只读执行器。[本体回收与恢复](ontology-asset-lifecycle-guide.md) · [动作回执与只读试算](../specs/decisions/DR-125-read-only-simulation-and-action-receipts.md) |

## 系统接入与领域应用

- **图谱交换。** 通过 SPARQL 查询图谱，按需导出 RDF/XML、Turtle 或 JSON-LD。与外部语义层交换时，可查阅 Apache Ossie 模式文件的版本、来源和许可。[本地标准与交换格式](../ontology/standards/README.md)
- **智能体接口。** MCP 构建服务可发起作业、跟踪进度和读取待审项。`POST /api/ont/check` 可单独检查一条 SQL 的本体约束。经 ATH 网关接入时，用户授权决定可访问的数据源和操作范围。[MCP 构建服务](../mcp_build_server.py) · [SQL 检查接口](../specs/decisions/DR-134-check-api-and-evaluation-statistics.md) · [ATH 接入](../ath_adapter/README.md)
- **机器人行为树。** 先用随仓 XML 和结构化任务运行离线示例，再按需要配置模型解析自然语言任务。输出是待验证的候选行为树，实机执行另需设备端验证。[离线示例](robotics-offline-demo.md) · [任务与行为树](robotics-task-quickstart.md) · [构建接口](robotics-build-api.md)
- **本体与应用的兼容性。** 查看某一版数据本体或机器人本体是否满足目标应用所需的字段、关系和参数，定位缺项并查看历次报告。[应用契约](application-contracts-guide.md) · [版本与运行存储示例](domain-storage-demo.md)

## 开发与测试

在仓库根目录激活虚拟环境后执行下列命令。浏览器用例使用 Chromium，前端行为测试需要 Node.js，CI 使用 Node.js 22。

```bash
python -m pip install -r requirements-dev.txt -r requirements-ui.txt
python -m playwright install chromium
python scripts/check.py --suite all
```

也可用 `--suite unit` 运行单元、前端行为、覆盖率和静态检查，用 `--suite integration` 运行隔离 HTTP 与十轮变序回归，用 `--suite ui` 运行桌面与窄屏浏览器验收。隔离检查的本地报告默认写入 `.check-results/`。要单独比对合成数据库场景与参考答案，可运行 `python scripts/scenario_bench.py`。[CI 工作流](../.github/workflows/ci.yml) · [场景基准](../specs/decisions/DR-067-scenario-regression-benchmark.md)

提交行为变更时，请对照需求与验收标准补充相应测试。系统分层、设计决策和贡献要求分别见[系统架构](../ARCHITECTURE.md)、[规格索引](../specs/map.md)、[需求与测试映射](../specs/acceptance.json)和[贡献约定](../AGENTS.md)。
