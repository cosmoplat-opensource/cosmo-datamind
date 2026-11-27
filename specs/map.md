# Specs Map · COSMO DataMind 数据智脑

> 产品规约索引，用于查找设计决策、接口契约与验收范围。

> **阅读说明（2026-08-28）：** `decisions/`、`iterations/` 与下文带日期的条目是历史决策记录，
> 会保留当时的页面名、接口名和测试数字，不代表当前能力。当前能力以 `README.md`、
> `ARCHITECTURE.md`、`docs/pipelines/ontology_build.yaml` 和测试结果为准。尤其不得把旧记录中的
> “完备度 100%”“全部关系接地”“多模态解析”或 HermiT 文字当作已验证能力。

## 项目一句话
COSMO DataMind 是「数据治理 × 本体 × 深度问数」原型：
以**配置的 SQLite 数据源只读执行**，可选接入 `DATAMIND_ENGINE_DIR` 指定的上游本体引擎。
核心能力是基于数据库元数据、结构化输入和人工提供文本的本体候选提议、数据检验与人工复核；
二进制附件在没有外部解析器时只登记来源，不宣称已理解其中的图像或正文。
并对齐 `iip.iiot-platform.com/bigdata` 的品类与观感。单端口 8092,`./start.sh` 启动。

> 与平台的关系:平台(`../上游本体引擎/`)是**引擎与方法论真相源**(其 `specs/` 有 DR-001…DR-011);
> DataMind 是**下游产品化前端**,复用其 engine/技能,但采用**独立的本地执行、路由、安全与命名规则**——
> 本 `specs/` 只记录 DataMind 自身的决定与迭代,与平台 specs 前缀独立、勿混用。

## 机器人构建与应用交互

[DR-172](decisions/DR-172-application-guidance-and-return-navigation.md) 与 [IR-033](iterations/IR-033-application-guidance-and-return-navigation.md)：应用检查提示、统一完善入口、浏览器后退与操作恢复。AGN-01～AGN-04。

[DR-171](decisions/DR-171-robotics-workflow-continuity.md) 与 [IR-032](iterations/IR-032-robotics-workflow-continuity.md)：任务检查入口、集中知识确认、紧凑设计画布、完成导航与自动执行适配。RUX-01～RUX-06。

## 测试执行与隔离

[DR-168 · 独立测试数据与有界并行验证](decisions/DR-168-isolated-test-seeds-and-bounded-parallel-checks.md)：复用真实构建准备产物，保存和读取仍完整校验；独立资产与运行身份；有界进程及完整覆盖率汇总。PERF-T01～T04。

## 开发约定(DR-066)
- 需求 → 测试映射 `specs/acceptance.json`;有效行规则 `specs/models/valid_rows_rules.json`;场景回归基准 `tests/scenarios`(`scripts/scenario_bench.py`);构建步骤说明 `docs/pipelines/ontology_build.yaml`。

## 关键契约
- **数据执行**:侧栏选择已登记的 SQLite 来源，总览、目录、问数、指标与可视化沿用该来源；SQL 和可视化可显式覆盖。`DATAMIND_DB` 为默认库。防写由 `mode=ro`、`sql_is_readonly` 与单句执行共同实现。SPARQL 在当前本体的本地 rdflib RDF 图(IR→Turtle)执行。见 DR-001、DR-095。
- **建模算法**:行业/本体标准（均可不选，参照或强约束）+ CQ/技能正文 → LLM 提议对象、事件、关系和候选键 → 数据验证(取值重叠≥60%∧父键唯一∧命名有据∧方向正确→verified,其余保留 candidate)→ 所选参照的候选注释/缺口检查 → 图结构/证据/定义/CQ 验收；LLM 离线时由 quick_build 使用同一后处理和验收规则。见 [[DR-002-multimodal-llm-anti-hallucination-build]]、[[DR-050-executable-ontology-build-gate]] 与 [[DR-052-build-industry-and-standard-references]]。
- **引擎**:运行时经 `agent_runtime`(CLAW_DRIVER 选 hermes/claude-code/openclaw),多引擎依次降级;**对外统一中性名**(智能引擎/备选/经典),不暴露底层库名,见 [[DR-003-runtime-neutral-naming]]。
- **深度问数**:SSE 流式执行记录 + 渐进出图,缓存键含上传指纹,离线时降级为模板,见 [[DR-004-deep-qa-sse]]。
- **前端**:对齐 iiot-platform/design-system 设计 token(#4A5FF3/#409EFF、圆角 4px、PingFang);**工业级去 emoji**(accent bar + 单色 SVG + 状态点),见 [[DR-005-frontend-design-system]]。
- **安全**:只读 SQL、全局 CSRF 校验、图谱键路径穿越校验、命令白名单、SPARQL 禁 SERVICE/FROM 外链,见 [[DR-006-security-model]]。
- **寻址**:18 个子页经 `#<page>` hash 直接寻址 + 深链,见 [[DR-007-url-hash-routing]]。

## 机器人交互式本体图谱

当前仅接入机器人本体的设计阶段可视化与固定版本浏览。数据本体主图、构建结果预览、问数高亮、证据状态、详情和评审维持现有实现，不安排后续自动接入。

- [DR-166 · 机器人交互式本体图谱与构建可视化](decisions/DR-166-interactive-ontology-graph-visualization.md)：方案已采纳；2026-11-19 范围和命名修订实现交付状态：delivered。
- [IR-031 · 机器人交互式本体图谱与构建可视化](iterations/IR-031-robotics-interactive-ontology-graph-visualization.md)：delivered。2026-11-17 的原交付记录覆盖 17 个全量阶段；2026-11-19 的命名修订仅做定向回归。
- [DR-167 · 交互式本体图谱边界与公开材料](decisions/DR-167-interactive-graph-limits-and-publication-boundary.md)：仅适用于机器人设计和版本浏览，保留预算、超时、只读及发布边界；STV-H01～H05 本次交付状态为 delivered。
- 需求与测试登记于 `acceptance.json`；已移除数据本体接入的计划验收，不删除现有数据本体测试。

## 机器人双路径构建（2026-11-10，已确认方案）

- [DR-152 · 机器人本体统一入口与模型／离线双路径](decisions/DR-152-robotics-dual-build.md)：accepted；必须上传双 XML，有模型走新 TBox/ABox 方案，无模型保留旧 UR 离线构建；模型错误不静默降级，设计候选与应用资格分开。
- [IR-024 · 机器人本体模型辅助与离线双路径构建](iterations/IR-024-robotics-dual-build.md)：delivered（2026-11-11 用户确认）：双路径、完整 JSON/OWL、设计修订和应用准入已交付；T10 全量 13 阶段通过，1988 单元通过、覆盖率 85.0%，真实模型运行已核验。

## 当前机器人集成方向（2026-11-05）

- [DR-148 · 简洁的本体构建到行为树生成流程](decisions/DR-148-minimal-robotics-task-workflow.md)：用户已确认先打通主流程，默认只显示当前版本；参数、映射和必要适配在 DataMind 按当前任务缺口补齐，高级维护暂缓。
- [IR-020 · 基础版任务流程](iterations/IR-020-minimal-robotics-task-workflow.md)：delivered（2026-11-18 维护者确认，代码已经 PR #1 合入，见 DR-159）。

## 索引

- [DR-160 · 产品仓库与本地开发材料边界](decisions/DR-160-product-repository-material-boundary.md)：维护者确认保留决策与迭代，指定过程材料和契约草案仅保留本地副本；提交范围由元测试检查。

### 提议 / Proposals（未立项，落地时拆为独立 DR/IR）
- PROPOSAL · DataMind 与源项目本体构建及行为树生成深度集成 — **proposed，2026-11-02 代码复核版**。包含跨项目复测结果、后端与 UI 方案、8 个实施切片，以及 DR-141…DR-147 的修订依据；区分现有能力、已知断点和待实施设计。
- PROPOSAL · 领域可插拔扩展架构（词表下沉 + 契约抽象 + 导出格式与本体应用分离 + 领域包） — **待实施**。为接入外部机器人领域而做的架构提议：9 个编号变更项（C1 词表 · C2 契约 · C3 导出格式 · C4 应用框架 · C5 领域包 · C6–C8 外部机器人平台域 · C9 UI）、术语钉死（"领域/节点/词表"非一对一、"导出格式 ≠ 本体应用"、"契约方言"实为证据通道不同）、6 阶段顺序与 10 条回归红线。**本文是待实施清单，不是现状说明**；现状仍以 `README.md`/`ARCHITECTURE.md`/`docs/pipelines/ontology_build.yaml` 为准。

### 决定 / Decisions
- [DR-159 · PR #1 合入后的审查与修复](decisions/DR-159-pr1-review-fixes.md)：已实现；公开内容清理、生命周期与问数闸门、机器人领域与接口、界面与测试。
- [DR-158 · master 与机器人本体功能的兼容集成](decisions/DR-158-master-development-integration.md) ／ [IR-030](iterations/IR-030-master-development-integration.md)：in-progress；编号迁移表。
- [DR-141 · 统一本体资产、版本与领域权威载荷](decisions/DR-141-unified-domain-semantic-graph.md) · **accepted**（2026-11-18 维护者确认，见 DR-159）
- [DR-142 · 机器人本体自动构建、增量编辑与发布契约](decisions/DR-142-robotics-source-recipes-and-reverse-build.md) · **accepted**（2026-11-18 维护者确认，见 DR-159）
- [DR-143 · 参数依据、适配证据与分层准入](decisions/DR-143-parameter-contracts-calibration-and-readiness.md) · **accepted**（2026-11-18 维护者确认，见 DR-159）
- [DR-144 · 受约束任务解析与版本化行为树应用](decisions/DR-144-behavior-tree-compilation-as-ontology-application.md) · **accepted**（2026-11-18 维护者确认，见 DR-159）
- [DR-145 · 渐进领域接入与本体全流程 UI](decisions/DR-145-local-domain-registration-and-application-ui.md) · **accepted**（2026-11-18 维护者确认，见 DR-159）
- [DR-146 · 持久化运行、会话隔离与工件追溯](decisions/DR-146-persistent-build-runs-and-artifact-manifests.md) · **accepted**（2026-11-18 维护者确认，见 DR-159）
- [DR-147 · 跨工程契约对齐与渐进迁移验证](decisions/DR-147-robotics-migration-and-compatibility-validation.md) · **accepted**（2026-11-18 维护者确认，见 DR-159）
- [DR-140 · 构建放弃等待后停止模型调用;本体修改前后的配对回放;数据接地的移植偏差与对比基准重测](decisions/DR-140-cancel-and-paired-replay.md)
- [DR-139 · DR-133 至 DR-138 的复查修正](decisions/DR-139-review-of-dr133-138.md)
- [DR-138 · 概念重提与多次回放](decisions/DR-138-concept-repair-and-repeated-replay.md)
- [DR-137 · 反馈的回放验证](decisions/DR-137-feedback-replay.md)
- [DR-136 · 名称不透明库的数据接地(移植自对比实验)](decisions/DR-136-data-grounding-for-opaque-schemas.md)
- [DR-135 · 算术见证与多级表头](decisions/DR-135-arithmetic-witness-and-multilevel-headers.md)
- [DR-134 · 对外的 SQL 本体检查接口;问数评测的统一比对规则、配对区间与候选上限](decisions/DR-134-check-api-and-evaluation-statistics.md)
- [DR-133 · 校验改用语法解析、没有本体时如实提示、教程重跑](decisions/DR-133-sql-parser-no-ontology-tutorial.md)
- [DR-132 · 同名别名不能绕过校验、界面可访问性、用语统一](decisions/DR-132-shadowed-aliases-accessibility-terms.md)
- [DR-131 · 重复计数检查按查询块判断、界面的失败态与转义、文档与当前界面对齐](decisions/DR-131-fanout-per-block-ui-failures-docs.md)
- [DR-130 · 两张子表互相放大、指标的汇总方式、查数结果三态、实际取数日期](decisions/DR-130-join-multiplication-query-states-data-date.md)
- [DR-129 · 发布重新验收、读数前校验范围、无回执的服务端错误、列名匹配、候选可用键盘选](decisions/DR-129-recheck-on-publish-scope-receipts-columns.md)
- [DR-128 · 数值匹配:检查符号、显式单位和所属行](decisions/DR-128-value-match-sign-unit-row.md)
- [DR-127 · 推演的参数必须唯一确定](decisions/DR-127-what-if-parameters-must-be-unique.md)
- [DR-126 · 追踪按路线指定的角色取关系](decisions/DR-126-trace-follows-route-roles.md)
- [DR-125 · 问数推演只做只读试算;动作按回执确定结果](decisions/DR-125-read-only-simulation-and-action-receipts.md)
- [DR-124 · 发布绑定内容修订,撤回即停用,并行的继续构建不互相覆盖](decisions/DR-124-release-bound-to-revision.md)
- [DR-123 · 固定词表认不出时由模型辅助分型](decisions/DR-123-model-assisted-question-typing.md)
- [DR-122 · 答案的结果记录与数字核对(核对部分由 DR-128 修订)](decisions/DR-122-answer-record-and-number-check.md)
- [DR-121 · 工艺路线可以直接用于追踪](decisions/DR-121-route-as-trace-main-line.md)
- [DR-120 · 可执行动作与问数推演](decisions/DR-120-executable-actions-and-what-if.md)
- [DR-119 · 问数使用决策层](decisions/DR-119-qa-uses-the-decision-layer.md)
- [DR-118 · 从决策倒推的构建方法作为内置技能](decisions/DR-118-decision-driven-build-skill.md)
- [DR-117 · 工艺路线层与决策层进入主线](decisions/DR-117-process-route-and-decision-layer.md)
- [DR-116 · 按方向核验的工艺类验收问题](decisions/DR-116-directed-acceptance-questions.md)
- [DR-115 · 正式问数只取用有资格的图谱](decisions/DR-115-release-state-for-formal-qa.md)
- [DR-114 · 关系的身份带上角色](decisions/DR-114-relation-identity-includes-role.md)
- [DR-113 · 随仓分发的构建产物都要过得了质量闸](decisions/DR-113-shipped-graphs-meet-the-evidence-rule.md)
- [DR-112 · 探索性测试查出的界面缺陷](decisions/DR-112-ui-fixes-from-exploratory-testing.md)
- [DR-111 · 探索性测试查出的后端缺陷](decisions/DR-111-api-robustness-from-exploratory-testing.md)
- [DR-110 · ATH 适配层加固](decisions/DR-110-ath-adapter-hardening.md)
- [DR-109 · 去掉四个页面和 12 条已被取代的接口](decisions/DR-109-prune-pages-routes-and-unused-modules.md)
- [DR-108 · 去掉五个只读或演示性质的页面和一条无调用方的路由](decisions/DR-108-remove-read-only-and-demo-pages.md)
- [DR-107 · 评审台展示证据、列出分流移出的关系、支持批量评审](decisions/DR-107-review-workbench-evidence-and-batch.md)
- [DR-106 · 覆盖率与类型检查的门槛覆盖路由层](decisions/DR-106-gates-cover-route-layer.md)
- [DR-105 · 构建页凭作业号接续，可取消](decisions/DR-105-build-page-resumes-jobs.md)
- [DR-104 · ATH：令牌限定数据来源、深度问数范围、请求号贯通](decisions/DR-104-ath-source-binding-qa-scope-audit-link.md)
- [DR-103 · 构建作业落盘、可取消，发起方留痕](decisions/DR-103-durable-cancellable-build-jobs.md)
- [DR-102 · ATH 适配层进入产品仓库](decisions/DR-102-ath-adapter-in-repo.md)
- [DR-101 · 构建作业由服务端持有，连接只是订阅者](decisions/DR-101-server-held-build-jobs.md)
- [DR-100 · 孤儿记录与抽样证据不再直接认定为 verified；导出 IRI 保留中文名](decisions/DR-100-orphans-samples-and-iris.md)
- [DR-099 · 证据强度补漏：单值子键、指标的独立参照、指标 id、否决模式回流](decisions/DR-099-evidence-strength.md)
- [DR-098 · 问数校验补漏：连接写法、范围、不存在的列、降级与投票](decisions/DR-098-qa-gate-hardening.md)
- [DR-097 · 人审结论在继续构建中保留，回放失败留痕](decisions/DR-097-human-review-survives-rebuild.md)
- [DR-096 · 已保存的端点凭据只发往已保存的端点](decisions/DR-096-endpoint-credentials-stay-with-their-endpoint.md)
- [DR-095 · 多数据库上下文与可视化切换](decisions/DR-095-multi-database-context-and-visualization.md)
- [DR-094 · 保持正常功能的代码清理与重启配置检查](decisions/DR-094-behavior-preserving-code-cleanup.md)
- [DR-072 · 全库评估与修正:注释接入、类型无关的取值比对、按取值补提与实例表识别](decisions/DR-072-whole-dataset-evaluation.md)
- [DR-073 · 候选关系分流、问数选表修正与澄清、抽样截断标记与独立评审](decisions/DR-073-candidate-triage-and-independent-judging.md)
- [DR-074 · 200 表合成验证库评估:星型模型键名、字段注释证据与几类误判](decisions/DR-074-contract-200-synthetic-db-evaluation.md)
- [DR-082 · 精确作答只返回所问名称、明细表优先;双向与工业库重复实验](decisions/DR-082-exact-answer-names-and-detail-tables.md)
- [DR-093 · 研究能力入产品：字段目录、业务概念、角色外键与可撤销语义复核](decisions/DR-093-research-semantic-capabilities.md)
- [DR-081 · 精确作答的多路候选投票;本体约束校验作为智能体工具;跨方法投票](decisions/DR-081-candidate-vote-and-hybrid.md)
- [DR-080 · 问数先探查再作答、按结果核对、角色键与取值出处](decisions/DR-080-qa-probe-verify-and-role-keys.md)
- [DR-079 · 与 EvoOntology 对比实验:精确作答、驼峰与复数表名、带角色前缀的键名](decisions/DR-079-bird-comparison-exact-answer-and-key-names.md)
- [DR-078 · 界面与文档用语规范、问数关系图与输入栏排版](decisions/DR-078-terminology-and-figure-legibility.md)
- [DR-077 · 库内数据字典进入构建取证、字段注释否决冲突关系、问数上下文与意图核对修正](decisions/DR-077-in-db-dictionary-and-comment-veto.md)
- [DR-076 · 演示库按业务过程仿真重建:108 表基础库与 200 表验证库 v3](decisions/DR-076-process-simulated-demo-databases.md)
- [DR-075 · 200 表合成验证库生成器 v2:业务链派生、如实登记底库缺陷与生成后语义校验](decisions/DR-075-contract-200-generator-v2.md)
- [DR-071 · 全量分轮与留出评估:键匹配排序、同名概念消歧与方向反证](decisions/DR-071-full-scale-holdout-evaluation.md)
- [DR-070 · 孤儿记录、类别作用域编码、码表关系、取值形态与对象类型](decisions/DR-070-orphans-scoped-codes-codebooks-kinds.md)(孤儿记录容忍一项已由 DR-100 取代)
- [DR-069 · 真实模型评估暴露的构建缺口:跨轮键匹配、多态引用与问数维度核对](decisions/DR-069-build-agent-real-model-gaps.md)
- [DR-068 · 逻辑删除的「未删除」取值按实际数据记录](decisions/DR-068-observed-live-markers.md)
- [DR-067 · 场景回归基准:两种数据形态端到端比对标准答案](decisions/DR-067-scenario-regression-benchmark.md)
- [DR-066 · 开发范式取舍:哪些对本项目有用、哪些不用](decisions/DR-066-ai-era-development-paradigms.md)
- [DR-065 · 两种场景逐环节走通:代码契约、有效行四规则、按来源库问数](decisions/DR-065-end-to-end-parity-code-contracts-and-scoped-keys.md)
- [DR-064 · 统一两种场景的半自动构建方法:概念层线索、场景画像与技能分层](decisions/DR-064-unified-method-across-scenarios.md)
- [DR-063 · 构建后自动复核,问数时套用清洗规则](decisions/DR-063-auto-cross-check-and-qa-cleaning.md)
- [DR-062 · 把流水线流程保存为技能:多通道交叉复核与数据清洗](decisions/DR-062-cross-check-and-cleaning-as-skills.md)
- [DR-061 · 能源与碳排放领域技能,以及「一表一对象」建模方式的复核](decisions/DR-061-energy-carbon-skill-and-modeling-review.md)
- [DR-060 · 构建前预处理:外部库只读抽样、构建前检查与分轮构建](decisions/DR-060-build-preflight-sampling-and-rounds.md)
- [DR-059 · 长文证据分块检索、跨轮关系入图、无数据取证与能源/碳排放行业参照](decisions/DR-059-long-document-evidence-and-energy-carbon-reference.md)
- [DR-058 · 构建能力对外 MCP 化与 LLM 回复解析单一事实源(Agent 只能发起构建与阅读结论,不能改写结论状态)](decisions/DR-058-build-mcp-and-llm-json.md)
- [DR-057 · 常驻部署与真实链路验收](decisions/DR-057-persistent-deployment-and-live-validation.md)
- [DR-056 · 完整取证、指标状态新鲜度、后端安全与隔离验收](decisions/DR-056-evidence-integrity-and-isolated-validation.md)
- [DR-055 · 扇出关卡、概念画像与 OSI 风格导出(父侧列聚合即拦 + 确定性画像检索 + 未经 validator 的 YAML 出口)](decisions/DR-055-fanout-gate-concept-profile-osi-export.md)
- [DR-054 · 指标契约、指标反解与指标生命周期(可编译/可执行/与参照比对;verified 只来自证据,certified 只能人授)](decisions/DR-054-metric-contract-and-lifecycle.md)
- [DR-050 · 半自动构建的统一证据契约、技能输入与确定性验收检查](decisions/DR-050-executable-ontology-build-gate.md)
- [DR-051 · 内置技能可改写与可删除(覆盖层 + 删除墓碑)](decisions/DR-051-editable-builtin-skills.md)
- [DR-052 · 半自动构建的行业参照与本体标准配置(none/参照/强约束 + 迭代继承)](decisions/DR-052-build-industry-and-standard-references.md)
- [DR-053 · 技能元数据的单一解析器、MCP 协议协商与图谱列表缓存](decisions/DR-053-metadata-single-source-and-graph-cache.md)
- [DR-043 · 单体路由蓝图化(计划+已起步:抽纯模块降耦合→逐簇拆blueprint)](decisions/DR-043-blueprint-modularization.md)
- [DR-044 · JSON store 持久化抽象(原子/坏档恢复/校验/迁移/并发,填负向持久化测试空白)](decisions/DR-044-json-store-abstraction.md)
- [DR-040 · 定义质量评分(属加种差/非循环/反例 + 参考重叠;LLM 1.0 vs 数据驱动 0.0)](decisions/DR-040-definition-quality-eval.md)
- [DR-039 · 反幻觉评测台(带标签对抗基准 + 泄漏率;反幻觉变数字)](decisions/DR-039-hallucination-eval-harness.md)
- [DR-038 · 基数自适应 θ(基准纯增益但真实数据回归 → 落地原语不接入)](decisions/DR-038-cardinality-adaptive-threshold.md)
- [DR-037 · 方向感知裁决;数据实证否决朴素强门槛翻转(fk_direction/should_reverse)](decisions/DR-037-direction-aware-adjudication.md)
- [DR-036 · 自引用与角色键发现(role_targets + 角色感知 name_ok + health 自反豁免)](decisions/DR-036-self-referential-and-role-keys.md)
- [DR-035 · 单一裁决核 dao_core(消两份漂移实现 + classify 三态 + 引擎平价)](decisions/DR-035-unified-adjudication-core.md)
- [DR-049 · 语义层关系型投影(OWL 标准出口 + 关系表消费出口)+ 深度问数链路显式编排](decisions/DR-049-relational-projection-and-explicit-pipeline.md)
- [DR-048 · HTTP 慢速攻击缓解(请求头总时限 + 正文速率闸 + 并发上限;扫描中危项已修复)](decisions/DR-048-slow-http-dos-mitigation.md)
- [DR-047 · 侧边栏信息架构与字号层级(本体页归一 + 动作中心独立 + 内容字号不倒挂)](decisions/DR-047-sidebar-ia-and-type-scale.md)
- [DR-046 · 确定性模块的隔离单测(离线秒级 + quick_build 可测化)](decisions/DR-046-deterministic-module-unit-tests.md)
- [DR-045 · 工程校验与 TDD 基础设施(pytest/coverage/ruff/CI + 文档计数自检)](decisions/DR-045-engineering-harness-and-tdd.md)
- [DR-001 · 本地只读执行(避开平台 1142/方言/沙箱坑)](decisions/DR-001-local-readonly-execution.md)
- [DR-002 · 多模态 LLM 本体自动构建 + 反幻觉取证(算法核心)](decisions/DR-002-multimodal-llm-anti-hallucination-build.md)
- [DR-003 · 多引擎运行时 + 对外中性命名 + 限流兜底](decisions/DR-003-runtime-neutral-naming.md)
- [DR-004 · 深度问数 SSE 流式 + 缓存 + 兜底](decisions/DR-004-deep-qa-sse.md)
- [DR-005 · 前端对齐 iiot-platform + 工业级视觉](decisions/DR-005-frontend-design-system.md)
- [DR-006 · 安全模型(只读/CSRF/路径/白名单/SPARQL)](decisions/DR-006-security-model.md)
- [DR-007 · URL hash 子页寻址](decisions/DR-007-url-hash-routing.md)
- [DR-008 · 数据源与连接模型(内置/SQLite 校验/外部登记)](decisions/DR-008-datasource-connection-model.md)
- [DR-009 · 对象 id 英文名、显示中文名(cn)](decisions/DR-009-object-cn-display.md)
- [DR-010 · IOF/BFO 本体工程对齐(注释/映射/OWL 导出/SHACL 校验/元数据覆盖)](decisions/DR-010-iof-bfo-alignment.md)
- [DR-011 · 泛化裁决 v2(等值/前缀/复合键/PK 感知 + 三级控制环)](decisions/DR-011-generalized-adjudication.md)
- [DR-012 · SPARQL 健壮性(线程安全/诚实报错/合法默认示例)](decisions/DR-012-sparql-robustness.md)
- [DR-034 · 中文召回改反向匹配;算子按中文名定位](decisions/DR-034-chinese-recall-and-op-targeting.md)
- [DR-033 · 选中的本体图谱成为问数的锚定源(含键名词根校验)](decisions/DR-033-selected-ontology-as-anchor.md)
- [DR-032 · 深度问数的本体锚定可视化(对话内画出锚定子图与 SQL 实际命中)](decisions/DR-032-qa-anchor-visualization.md)
- [DR-031 · 向后兼容性检查与本体模块化(下游影响 + 领域/层次拆分)](decisions/DR-031-compat-and-modularization.md)
- [DR-030 · 本体结构检查(七类图结构异常,阻断问题/提示分级)](decisions/DR-030-ontology-health-check.md)
- [DR-029 · OpenAI 兼容运行时与回归隔离(任意 LLM 可接 + 沙箱图谱)](decisions/DR-029-openai-compat-runtime-and-test-isolation.md)
- [DR-028 · 业务规则约束与决策层(确定性推理 + 决策路径可回溯 + 冲突不静默)](decisions/DR-028-rules-and-decision-layer.md)
- [DR-027 · 对象业务别名与本体变更审计(别名贯通五处 + 审计区分 AI/人工来源)](decisions/DR-027-business-aliases-and-change-audit.md)
- [DR-026 · 双盲意图检测与本体使用度回流(两通道互不透传 + 只观测不阻断)](decisions/DR-026-doubleblind-intent-and-usage.md)
- [DR-025 · 概念漂移检测与穿透链路核验(确定性 schema 比对 + 逐段判定)](decisions/DR-025-drift-and-penetration-chain.md)
- [DR-024 · 能力问题(CQ)驱动的构建与验收(结构可达性判定,不调 LLM)](decisions/DR-024-competency-questions-driven-build.md)
- [DR-023 · 语义增强三件套:词汇表注入/增强注册表/一致性门控(对标 Trane 认知稳定性)](decisions/DR-023-epistemic-stability.md)

### 迭代 / Iterations
- [IR-016 · 第一阶段：机器人包边界与构建消费契约贯通](iterations/IR-016-robotics-contract-integration.md) · **delivered（2026-11-03 用户确认）**：Proposal 步骤 0～1，89 项机器人测试与实际三场景演示通过，各回归检查段经补验通过，最终交付已确认。
- [IR-017 · 第二阶段：本体资产、不可变版本与持久化运行基础](iterations/IR-017-ontology-assets-and-persistent-runs.md) · **delivered（2026-11-04 用户确认）**：资产/版本、运行/事件/工件、真实生成接入、离线三场景与独立进程核验已交付；109 项已审用例及全量九段通过，覆盖率 84.6%。后续构建 API/UI 与浏览器联动继续按 Proposal 步骤 3～7 实施。
- [IR-018 · 第三阶段：机器人本体构建 API 与平台 UI](iterations/IR-018-robotics-build-api-and-ui.md) · **delivered（2026-11-04 用户确认整轮交付）**：Proposal 步骤 3，双 XML 上传、来源型运行、候选目录/详情/下载及真实浏览器联动；构建/存储、HTTP/执行器及 UI 已实现；40 项前端状态与 14 项真实浏览器通过，全量十段检查退出 0。
- [IR-019 · 第四阶段：机器人知识维护、重建合并与评审发布](iterations/IR-019-robotics-knowledge-review-and-publication.md) · **delivered（2026-11-18 维护者确认，代码已经 PR #1 合入，见 DR-159）**：Proposal 步骤 4，受控业务编辑、参数/证据、并发版本、三方冲突合并与审核发布；122 项领域/存储测试及 83 项治理 HTTP 测试转绿，全量十段通过；知识维护、审核发布与版本下载已接入 HTTP/UI，离线 CLI 四场景可运行；T07 全量 11 段通过。
- [IR-015 · 实际部署与前后端验收](iterations/IR-015-live-deployment-acceptance.md) · **delivered**
- [IR-014 · 算法、安全、UI 与 SDD/TDD 综合验收](iterations/IR-014-evidence-security-ui-validation.md) · **delivered**
- [IR-001 · DataMind 基座(目录/图谱/指标/质量/SQL)](iterations/IR-001-foundation.md) · **delivered**
- [IR-002 · 深度问数对齐平台 chat-bi](iterations/IR-002-deep-qa-chatbi.md) · **delivered**
- [IR-003 · 本体构建问询台(LLM 辅助建模；二进制附件不在本仓解析)](iterations/IR-003-ontology-build-console.md) · **delivered**
- [IR-004 · 数据连接 + 数据可视化模块](iterations/IR-004-connection-and-viz.md) · **delivered**
- [IR-005 · 安全加固 + 工业化 + 复审收敛](iterations/IR-005-hardening-and-review.md) · **delivered(持续)**
- [IR-006 · IOF/BFO 语义工程层(注释/映射/导出/元数据覆盖)](iterations/IR-006-iof-bfo-semantic-layer.md) · **delivered**
- [IR-007 · 工程校验与 TDD 基础设施(pytest/coverage/ruff/CI + 确定性模块单测)](iterations/IR-007-tdd-foundation.md) · **delivered**
- [IR-008 · 裁决核收敛与算法强化(dao_core 单一事实源 + 引擎平价 + 零回归)](iterations/IR-008-adjudication-core-convergence.md) · **in-progress**
- [IR-011 · 单体路由蓝图化(共享上下文基座 + 逐簇拆 blueprint)](iterations/IR-011-blueprint-modularization.md) · **in-progress**
- [IR-012 · 恢复行业参照与本体标准构建配置](iterations/IR-012-build-reference-profiles.md) · **delivered**
- [IR-013 · 标准资产本地化与目录加载恢复](iterations/IR-013-reference-assets-and-loader-recovery.md) · **delivered**
- [IR-009 · 反幻觉评测台与门槛量化(对抗基准 + 泄漏率 + DR-038 回归实测)](iterations/IR-009-eval-and-definition-quality.md) · **in-progress**

## 上游素材(真相来源)
- [可选上游引擎的接入方式](../README.md#两种运行方式)。安装可访问的引擎后，用 `DATAMIND_ENGINE_DIR` 指定其目录。
- `DATAMIND_DB` — 实际只读数据源；仓库附带 `examples/demo_metrics.db`（108 表示例库），另可用 `examples/sample_db.sql` 创建 6 张表的最小示例库。
- `README.md` — 模块能力总览与实测部署命令。
- `ARCHITECTURE.md` — 分层/数据流/统一约定/安全模型/已知边界(架构说明)
- [项目测试说明](../README.md#测试)提供隔离回归命令。
- `test_all.py` — 系统级回归，源码定义 548 个检查点。

## 现状校准
- 计数(2026-11-17):206 路由 / 548 集成检查点 / 1917 项 pytest 测试 / 场景回归 51 项;决策记录到 DR-140。
  隔离环境下 `scripts/check.py --suite all` 的结果:集成 536 通过、6 项条件跳过(无上游引擎),界面走查 47 通过,界面实操 48 通过、3 项跳过(未配置外部模型端点)。
- 计数(2026-10-12):**140 路由**(server.py 113 + bp_engine 5 + bp_actions 8 + bp_metrics 3 + bp_semantic 2 + bp_decision 9)/ **548 集成断言** /
  **1279 项 pytest 测试**（多数据库功能复核）/ DR-001…DR-082、DR-093…DR-095 · IR-001…IR-013。
  DR-093 增加数据目录、问题驱动概念/主题、声明角色外键独立核验与业务语义人审；DR-095 增加多库上下文，场景回归为 51 项。
  DR-076 起 `examples/demo_metrics.db` 由 `scripts/build_demo_108_db.py` 按业务过程仿真生成(108 表 / 308,230 行)。
  DR-060 新增构建前预处理:外部库只读抽样 `ext_sample`、构建前检查与分轮计划 `build_prep`。
  DR-059 新增长文分块检索 `doc_chunks`,以及能源管理 `energy_management`、碳排放核算 `carbon_accounting` 两个独立行业参照。
  新增共享层 `srv_context`/`srv_engine`、单一裁决核 `dao_core`、评测台 `hallucination_eval`/`definition_eval`、
  持久化抽象 `store`、构建参照 `build_references`、本地标准资产 `standard_assets`、blueprint `bp_engine` / `bp_actions`，以及前端目录/响应式/构建参照模块。

- 计数(2026-07-17):27 页面模块 / 82 后端路由 / test_all.py 109 断言 / 数据 108 表 186,833 行 / 107 指标 / 655 术语。
- 计数(2026-07-27):28 页面模块 / 109 后端路由 / test_all.py 249 断言(数据与指标/术语计数不变)。
  - 新增(DR-010/IR-006，历史页面名):`元数据覆盖` 页面模块 + 4 路由。该旧指标只统计元数据字段覆盖，不能证明本体完整或 IOF 合规；当前复核为 60 条关系中 21 条可映射至类型兼容的官方 BFO/IOF 属性，其余 39 条保留在本地命名空间。
- 新增(DR-013):`本体评审` 页面模块 + `/api/ont/review`;关系人审算子 confirm_relation/reject_relation 并入 apply 白名单;关系类算子两种 IR 形状(links/relations)通吃——构建产物(built_*)自此可人审可编辑。test_all.py 119 断言全过。
- 新增(DR-014):问数「沿本体关系召回」步骤+⋈ JOIN 提示;`/api/chat/feedback(+/resolve)` 反馈回流→评审页「本体迭代候选队列」;「根因诊断」模式+`/api/diagnose/stream`(意图→关系召回→边界内约束生成)。
- 新增(DR-015):「动作中心」页 + /api/actions·invoke·log·approve —— 类型化参数/低风险直接形成动作记录/高风险审批/决策记录审计；当前 `decision_capture` 不写回业务系统。已登记动作会投影为本体 action 节点，图谱对象卡显示配置绑定，诊断清单可发起派工记录。
- 新增(DR-016):`mcp_action_server.py` —— 动作层 MCP server(stdio,零依赖):list_actions / **invoke_action(唯一写)** / get_action_status;审批不暴露,治理单一实现留在 HTTP API,Agent 接入自动继承「只能提议、不能批准」。
- 新增(DR-017):「引擎设置」页 + /api/engine/config·test —— 运行时/模型/API Key 实时切换(调用时读 env+清缓存,免重启);Key 只写不回显、0600、清除同步弹 env;先测后切(真实延迟/真实报错)。
- 新增(DR-018):全部读方统一 `load_ir_edited`(单一当前真相)——修复 问数JOIN提示/总览/指标/表详情/graphs列表 读原始IR 的不一致;U 分区 18 断言锁定「写→图谱/总览/问数/SPARQL/评审/列表 即读→撤销复原」。套件 186 断言。
- 新增(DR-019):深度问数十项升级 —— A1 术语词典进检索(term_expand 步)/ A2 SQL 本体约束拦截 `_validate_sql_ontology`(ontology_gate 步:表白名单+JOIN 键落本体关系)/ A3 指标计算规则卡(done.metric_cards)/ B4 按任务选模(task_models{plan/narrative/diagnose}+模型族保护)/ B5 多轮指代 `_carryover` / B6 叙述流式 narrative_delta / C7 外部库实连(pymysql;凭据 conn_secrets.json 0600 只写)/ C8 API 型数据源(api_fetch→up.api_* 物化)/ C9 问数评测(benchmark/qa_set.json 8 题参考集×三组,/api/eval/*,「问数评测」页)/ C10「业务助手」页(角色组装问数/诊断/动作/待办)。套件 212 断言。
- 新增(DR-020):动作层产品化 —— 动作类型管理 CRUD(/api/action/type·update·delete,内置种子受保护、停用即刻拦截发起)/ 3 个行业动作种子(冻结批次·温区调参·供应商 SCAR,均绑真实库表)/ 问数答案卡「相关动作」直达发起(SQL 命中表 × object_table 匹配)/ 动作中心 KPI+类型编辑弹窗+审计筛选与 CSV 导出;MCP 读同一注册表零改动继承。套件 226 断言。
- 新增(DR-021):构建技能管理 —— 浏览(内置附目录清单)/在线新建/编辑/两击删除(/api/build/skill/<name>·save·delete);**自定义技能正文注入构建方法论**(_skill_method_text,剥 front-matter,编辑后下次构建即生效)——修复「能上传但从未被消费」的死代码问题;内置技能可改写与隐藏(DR-051:workdir 覆盖层+删除墓碑,默认内容保留,可恢复默认)。套件 236 断言(105 路由)。
- 新增(DR-022):技能生态五项 —— ①技能对比实验(/api/build/skill_compare·status·results:同目标×两组技能各跑真实构建,对比 对象/关系/verified/动词/类型/定义覆盖;组失败如实展示不充数)②构建流水线 skill_inject 注入痕迹步 ③产物保存为技能(/api/build/skill/from_graph:动词表/类型分布/定义样例,确定性提取)④评测败题通用修复(⋈⋈ 两跳路径召回 + 均值分母/单值聚合的计算规范进 prompt)⑤存而不用审计(specs/audit-input-consumers.md;第 3 起死代码 qa_skills 已接上:_match_qa_skill 命中复用+skill_reuse 步)。套件 249 断言(109 路由;终跑 C 组 8/8)。
- 引擎在线依赖:深度问数与本体构建的 LLM 步骤依赖 `agent_runtime` 引擎在线;引擎限流(429)/超时时自动降级(深度问数走模板、构建走 quick_build),结果仍产出并如实标注。
- 已知边界详见 `README.md` 末节(SPARQL 软超时、运行时切换 UI、G6/ECharts 本地内置等)。

## 如何让 AI 代理消费本规约

当前验收映射为 [`acceptance.json`](acceptance.json)，连接业务要求与可执行行为测试。
统一入口为 `python scripts/check.py --suite all`；结果和环境边界须随迭代记录，不能只记录测试数量。
```
Consult @specs/map.md to find relevant context.
记录一个决定: 总结讨论, 按 @specs/meta.md 添加一个 DR。
实现一个迭代: 按 @specs/meta.md 写 IR(Goal/Deliverables/Tasks/Acceptance), 每任务一次提交, 读码+跑 test_all.py 验收后打勾。
```

## 开源收敛(2026-08-01)
- 本仓开源范围仅 COSMO DataMind 本体。移除旧系统内嵌:/platform 静态代理、/api/claw 代理、经典部署自拉起、引擎工具箱(TOOLS)、平台成果目录;UI 摘除 本体对话/建模工作台/成果库/平台工作台(原版)/平台对话(原版) 五页。
- 计数(2026-08-01):**26 页面模块 / 106 后端路由 / 279 运行时断言**(数据 108 表 186,833 行 / 107 指标 / 655 术语不变)。
- 上游引擎为可选组件(DATAMIND_ENGINE_DIR 接入,缺失自动降级),不随本仓发布。

## dev 分支研发中(2026-08-02 起)
- **DR-024 CQ 核验**:`cq_check.py` + `POST /api/ont/cq`。依据《本体智能研究报告(1.0)》
  (AIIA × CCSA TC601)六阶段流程——报告把能力问题验证列为「验证本体是否真正可用的核心环节」,
  而本系统此前只有元数据覆盖检查与问数评测(端到端正确率),缺「够不够用」这一环。
  判定为确定性图计算(锚定→路径→边状态),三态 answerable/partial/unanswerable,
  不可答自动回流 gaps。
- **DR-025 漂移与链路**:`drift_check.py` + `GET /api/ont/drift/<key>`、
  `cq_check.check_chain` + `POST /api/ont/chain`。前者对齐报告阶段六点名的
  「监控本体与数据源一致性,发现概念漂移与关系断裂」(四类:表/列/主键/关系键缺失);
  后者对齐四个行业案例同构的建模模式(5-6 类实体 + 一条纵向穿透链路),逐段判定给出断点。
  三者互补:CQ 答「够不够用」· 链路答「通不通」· 漂移答「还对不对得上数据」。
  套件 311 断言。
- **DR-026 双盲与使用度**:`intent_check.py`(接入问数两分支,本体约束校验后执行前)、
  `usage_stat.py` + `GET /api/ont/usage/<key>`。对齐报告电力案例的「双盲检测机制」
  与阶段六的「业务调用频次驱动迭代」。两通道互不透传(A 只看问句、B 只看 SQL),
  均不调 LLM;只观测不阻断。实测检出真阳性:问「业务员维度表有多少人」而 SQL
  查销售订单去重——本体约束校验放行,双盲判 mismatch。套件 326 断言。
- **DR-027 别名与审计**:`set_alias` 算子 + `GET /api/ont/audit/<key>`。别名贯通
  CQ 锚定/意图锚定/问数上下文评分与文本;审计按人/类型/来源聚合并标出风险
  (删除类、人审试图指定 verified)。本体对话页恢复(此前误删)并内置审计面板。
  套件 354 断言 · UI 走查 57 项。
- **DR-028 规则与决策层**:`rule_engine.py` + `/api/ont/rulebook/<key>`(CRUD+静态一致性)
  与 `/api/ont/decide/<key>`(规则求值)。第三遍对照报告改按**三层架构**核对,
  发现语义层第四要素「规则与约束」与整个**决策层**缺失。确定性推理不调 LLM,
  每条结论带 trace 可回溯至规则/字段/阈值;同动作不同结论报冲突而不静默择一。
  实测顺带修复:问数缓存键漏本体指纹,导致改本体后仍复用旧答案。
  套件 373 断言 · UI 走查 57 项。
- **DR-029 通用运行时与回归隔离**:`openai_runtime.py`(任意 OpenAI 兼容端点可接,
  经 GLM coding plan 实测问数与构建的完整流程)+ 回归沙箱 `built_regress`
  (真凶是 rebuild 清空草案层,非 undo)。套件 378 断言 · UI 57 项。
- **DR-030 本体结构检查**:`health_check.py` + `GET /api/ont/health/<key>`。第四遍对照
  报告改按**五大价值**核对,发现阶段六「异常关系检测」空白。七类图结构异常分两级
  (阻断问题影响评分/提示只列出);历史示例中阻断问题为 0，但 108 个对象中有 50 个孤岛——
  此前任何检查都发现不了。套件 394 断言 · UI 走查 57 · UI 实操 21。
- **DR-031 兼容与模块化**:`compat_check.py` + `GET /api/ont/compat/<key>`(三级判定,
  重点是命中哪些已注册的规则/动作/技能)、`module_split.py` + `GET /api/ont/modules/<key>`
  (by_domain 连通分量+词根 / by_layer 数仓分层)。至此报告第四遍找出的三处空白全部补齐。
  套件 496 断言 · UI 走查 57 · UI 实操 47。
- **DR-032 锚定可视化**:`build_context(trace=)` + `_join_hints(pairs=)` 与上下文同源地
  记录锚定轨迹,SSE `anchor` 事件 + `done.anchor` 双路送达,前端 `dqAnchorHTML()` 在
  对话气泡内画静态 SVG 子图(入选理由配色 / 关系状态线型 / SQL 实际命中标记)。
  截断优先保留参与关系的对象(自查中修正)。套件 441 断言 · UI 走查 57 · UI 实操 25。
- **DR-033 选中本体作锚定源**:`_anchor_ir()` 让选中的图谱真正成为锚定/本体约束校验/埋点的本体;
  区分「选表=限定」与「选本体=在其中锚定」;`_join_hints` 形状无关(构建产物此前恒出 0 条);
  构建器落结构化 JOIN 键;`_key_name_ok()` 键名词根校验拦截自增键值域巧合造出的假关系;
  UI 增锚定链路(本体→命中→扩展→上下文→SQL 实际用)与命中证据。
  补做常驻锚定条(`#dq_ancbar`,不随对话流滚走)与「在图谱中高亮这一块」
  (跳本体图谱页,锚定对象着色、其余淡出)——此前锚定埋在气泡里,答案一出来就被
  自动滚动顶出视口(实测 top=-380px),等于没做。
  对话框全程不吃缓存(秒回的旧答案与历史对话里的记录在界面上无法区分)+
  界面版本自检(`/api/uiver`,旧标签页自己提示刷新)。
  任一图谱作锚定源都不再崩(table=null 的概念本体曾打崩 build_context,AO40 穷举全部图谱)。
  套件 492 断言 · UI 走查 57 · UI 实操 47。
- **DR-034 中文召回改反向匹配**:拿本体自己的中文词(中文名/别名/属性中文名/指标名)
  去问句里查,不引入分词器 —— 此前问句按标点切词,中文长句整句一个词元,
  **业务别名从来没在召回里生效过**,中文只能靠术语词典折成英文。
  算子定位按主键→中文名→表名→别名降级(助手回的 `obj:销售订单` 此前必报「对象不存在」,
  对话式改本体端到端走不通);离线白名单补齐 `apply_any` 的 9 个本地算子并用 AST 对账;
  据实回显本体名、铸造产物落 workdir、`/api/db/check` 改走 `ro_connect`
  (直连会静默新建空库,把「库没了」伪装成「库是空的」);
  「意图一致」写明比对了几个对象,不被读成「问句问的都答了」。
  技能/工具面三处:智能体列表硬编码旧目录名(技能包永远列不出,与构建页结论打架)、
  `rebuild` 无确认校验却丢弃整个草案层(undo 退不回来)、`skills/write` 凭空造引擎目录树。
  技能编排面板布局塌陷:全局 `input{width:100%}` 把勾选框撑到 224px、把同排文本挤成
  0 宽,描述逐字竖排(单卡高 557px)。元素齐全故计数类断言全绿 —— 改用几何量守住。
  顺势把几何判据推到全站(26 页 × 9 弹窗 × 两种视口扫一遍):另修表格单元格
  260px 截断后无从看全文(悬停按需补 title)、评审图谱下拉被固定宽度切掉计数。
  套件 553 条静态断言(有引擎环境实际执行 547/547,无引擎 539 通过/8 条件跳过) · UI 走查 64/64 · UI 实操 45/45，
  另有 3 项因未配置 OpenAI 兼容端点而按条件跳过。
- **快速入门可跑通**(QUICKSTART.md):示例库 SQL → quick_build → 选本体 → 问出数,
  全程无上游引擎。过程中修出四处断链:垫片不承载 driver 注册(配了 key 也拿不到运行时)、
  规划超时写死 60s(推理型模型必超)、`tables[]` 未归一(自建本体被判无绑表而回退)、
  本体缺 attrs 时上下文无列名(模型只能猜列)。

五项检查各管一面：验收问题(CQ)检查本体能否回答业务问题，链路检查关系路径是否连通，漂移检查本体与数据源是否仍然一致，
双盲意图检测检查回答是否偏离问题，图结构检查发现本体的结构问题。


## 应用契约与资产体验优化草案 2026-11-12

总方案，IR-025 T03 核心及全量回归已通过；IR-025 已于 2026-11-12 用户确认 delivered。
T01 应用契约基础 v1：已冻结；T02 RED 评审。

- [DR-153 应用契约注册与分层检查](decisions/DR-153-application-contract-registry.md) ／ [IR-025](iterations/IR-025-application-contract-registry.md)：delivered（2026-11-12 用户确认）。
- [DR-154 机器人应用检查与可追溯补全](decisions/DR-154-robotics-application-adaptation.md) ／ [IR-026](iterations/IR-026-robotics-application-adaptation.md)：T01 规约 已冻结，T05 集成 GREEN 的 57 项已审测试及全量 13 段通过，70 项核心仍绿；T06 真实副本验收 及全量 13 段已通过（2333 passed／5 可选跳过，覆盖率 86.1%），自动匹配与人工例外已纳入，2026-11-13 用户要求提交代码，确认 delivered。
- [DR-155 本体详情与应用修复体验](decisions/DR-155-ontology-detail-experience.md) ／ [IR-027](iterations/IR-027-ontology-detail-experience.md)：delivered，T03 GREEN 及全量回归已通过；T04 集成 RED 已于 2026-11-16 获继续指令，T05 GREEN 已实现直达任务对话、只读历史与修复入口及历史读取锁冲突修复，14 段全量回归通过；T06 真实副本技术验证 20 项通过，已于 2026-11-16 获用户确认交付。
- [DR-156 深度问数应用契约兼容](decisions/DR-156-deepqa-application-compatibility.md) ／ [IR-028](iterations/IR-028-deepqa-application-compatibility.md)：delivered；2026-11-16 用户确认后完成 T06，原句计数与实际 SQL 说明修复、8 道真实模型基线、48 组协议／模式复放和完整浏览器验收通过；最终全量 14 段完整退出 0。详见 T06 验收。
- [DR-157 本体删除恢复与历史保留](decisions/DR-157-ontology-asset-lifecycle.md) ／ [IR-029](iterations/IR-029-ontology-asset-lifecycle.md)：delivered（2026-11-17 用户明确确认整个 IR 最终验收）；删除／恢复及精简 UI 完成，工作副本和浏览器验证通过，该阶段全量检查 14 段退出 0。详见 T06 验收。

IR-025 的 T06 公共服务、HTTP、最小 UI 与生成守卫于 2026-11-12 经用户确认交付。

IR-029 于 2026-11-17 按用户反馈精简删除／恢复弹窗及回收站卡片；87 项相关检查和隔离浏览器验证通过，原全量报告为精简前代码，现已刷新全量并完成交付。见 UI 精简记录。

## 产品仓库材料边界

[DR-160 · 产品仓库与本地开发材料边界](decisions/DR-160-product-repository-material-boundary.md)：保留决策与迭代；指定开发中间材料及契约草案仅保留本地副本。

## master 与交互式本体图谱兼容集成

[DR-169 · master 修复与交互式本体图谱功能兼容集成](decisions/DR-169-master-and-interactive-ontology-graph-integration.md)：保留两边功能；master 编号保持，交互式本体图谱相关记录使用 DR-166、DR-167、DR-168，DR-160 保持不变；MERGE-GRAPH-01 本次交付状态为 delivered。

## 交互式本体图谱统一命名

[DR-170 · 交互式本体图谱命名与视图契约](decisions/DR-170-interactive-ontology-graph-naming-and-view-contract.md)：模块、只读接口、页面参数及验收入口统一使用功能名称；GRAPH-NAME-01 本次交付状态为 delivered。

## 任务补齐反馈

[DR-173 · 任务补齐的明确反馈与完善方式说明](decisions/DR-173-task-completion-feedback.md) ／ [IR-034](iterations/IR-034-task-completion-feedback.md)：保存反馈 TCF-02；入口验收 TCF-01 当前范围见下方 DR-174 / IR-035。

## 单一完善入口与生成提示

[DR-174 · 单一本体完善入口与生成结果提示](decisions/DR-174-single-repair-entry-and-generation-feedback.md) ／ [IR-035](iterations/IR-035-single-repair-entry-and-generation-feedback.md)：TCF-01；GEN-FEEDBACK-01 当前任务范围见下方 DR-175 / IR-036。

## 最终任务配置范围

[DR-175 · 按最终任务配置校验与生成](decisions/DR-175-task-configuration-validation-scope.md) ／ [IR-036](iterations/IR-036-task-configuration-validation-scope.md)：CFG-SCOPE-01、GEN-FEEDBACK-01。

## 读取设计时的图谱故障

[DR-188 读取降级](decisions/DR-188-design-graph-read-degradation.md) ／ [IR-049 验收与复现](iterations/IR-049-design-graph-read-degradation.md) ／ [DESIGN-RECEIPT-02 验收映射](acceptance.json)。

## 设计页读取失败恢复

[DR-189 有限重试与手动重新读取](decisions/DR-189-design-read-recovery.md) ／ [IR-050 验收与复现](iterations/IR-050-design-read-recovery.md) ／ [DESIGN-POLL-01 验收映射](acceptance.json)。

## 交互式图谱布局计算量

[DR-190 网格剪枝与标签复用](decisions/DR-190-graph-layout-work-bound.md) ／ [IR-051 验收与复现](iterations/IR-051-graph-layout-work-bound.md) ／ [GRAPH-LAYOUT-01 验收映射](acceptance.json)。

## 固定版本图谱的条件读取

[DR-191 ETag 与分块核对](decisions/DR-191-fixed-graph-revalidation.md) ／ [IR-052 验收与复现](iterations/IR-052-fixed-graph-revalidation.md) ／ [GRAPH-CACHE-01 验收映射](acceptance.json)。

## 交互式图谱空白查找

[DR-192 空白查找提示](decisions/DR-192-graph-blank-search.md) ／ [IR-053 验收与复现](iterations/IR-053-graph-blank-search.md) ／ [GRAPH-SEARCH-01 验收映射](acceptance.json)。

## 决策记录交付状态格式

[DR-193 交付状态只写状态值](decisions/DR-193-decision-status-format.md) ／ [IR-054 验收与复现](iterations/IR-054-decision-status-format.md) ／ [DR-STATUS-01 验收映射](acceptance.json)。
