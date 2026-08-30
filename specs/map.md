# Specs Map · Cosmo DataMind 数据智脑

> 入口文件。AI 代理与协作者从这里查找相关 spec。
> 规约驱动开发(SDD)。消费方式:`Consult @specs/map.md to find relevant context.`

> **阅读说明（2026-08-28）：** `decisions/`、`iterations/` 与下文带日期的条目是历史决策记录，
> 会保留当时的页面名、接口名和测试数字，不代表当前能力。当前可执行口径以 `README.md`、
> `ARCHITECTURE.md`、`docs/pipelines/ontology_build.yaml` 和测试结果为准。尤其不得把旧记录中的
> “完备度 100%”“全部关系接地”“多模态解析”或 HermiT 文字当作已验证能力。

## 项目一句话
Cosmo DataMind 是「数据治理 × 本体 × 深度问数」原型：
以**配置的 SQLite 数据源只读执行**，可选接入 `DATAMIND_ENGINE_DIR` 指定的上游本体引擎。
核心能力是基于数据库元数据、结构化输入和人工提供文本的本体候选提议、数据检验与人工复核；
二进制附件在没有外部解析器时只登记来源，不宣称已理解其中的图像或正文。
并对齐 `iip.iiot-platform.com/bigdata` 的品类与观感。单端口 8092,`./start.sh` 启动。

> 与平台的关系:平台(`../上游本体引擎/`)是**引擎与方法论真相源**(其 `specs/` 有 DR-001…DR-011);
> DataMind 是**下游产品化前端**,复用其 engine/技能,但采用**独立的本地执行、路由、安全与命名口径**——
> 本 `specs/` 只记录 DataMind 自身的决定与迭代,与平台 specs 前缀独立、勿混用。

## 关键契约
- **数据执行**:深度问数/可视化/SQL 工作台/即时问数(SQL 类)在 `DATAMIND_DB` 指定的 SQLite 数据源上只读执行；示例数据库需按 README 创建或显式指定。防写由 `mode=ro`、`sql_is_readonly` 与单句执行共同实现。SPARQL 在本地 rdflib RDF 图(IR→Turtle)执行。见 [[DR-001-local-readonly-execution]]。
- **建模算法**:CQ/技能正文 → LLM 提议对象、事件、关系和候选键 → 数据验证(取值重叠≥60%∧父键唯一∧命名有据∧方向正确→verified,其余保留 candidate)→ BFO/IOF 官方关系与类别约束检查 → 图结构/证据/定义/CQ 验收检查；LLM 离线时由 quick_build 使用同一数据验证规则。见 [[DR-002-multimodal-llm-anti-hallucination-build]] 与 [[DR-050-executable-ontology-build-gate]]。
- **引擎**:运行时经 `agent_runtime`(CLAW_DRIVER 选 hermes/claude-code/openclaw),多引擎顺序兜底;**对外统一中性名**(智能引擎/备选/经典),不暴露底层库名,见 [[DR-003-runtime-neutral-naming]]。
- **深度问数**:SSE 流式执行记录 + 渐进出图,缓存键含上传指纹,离线走模板兜底,见 [[DR-004-deep-qa-sse]]。
- **前端**:对齐 iiot-platform/design-system 设计 token(#4A5FF3/#409EFF、圆角 4px、PingFang);**工业级去 emoji**(accent bar + 单色 SVG + 状态点),见 [[DR-005-frontend-design-system]]。
- **安全**:只读 SQL、全局 CSRF 守卫、图谱键路径穿越校验、命令白名单、SPARQL 禁 SERVICE/FROM 外链,见 [[DR-006-security-model]]。
- **寻址**:28 个子页经 `#<page>` hash 直接寻址 + 深链,见 [[DR-007-url-hash-routing]]。

## 索引

### 决定 / Decisions
- [DR-050 · 半自动构建的统一证据契约、技能输入与确定性验收检查](decisions/DR-050-executable-ontology-build-gate.md)
- [DR-051 · 内置技能可改写与可删除(覆盖层 + 删除墓碑)](decisions/DR-051-editable-builtin-skills.md)
- [DR-043 · 单体路由蓝图化(计划+已起步:抽纯模块降耦合→逐簇拆blueprint)](decisions/DR-043-blueprint-modularization.md)
- [DR-044 · JSON store 持久化抽象(原子/坏档恢复/校验/迁移/并发,填负向持久化测试空白)](decisions/DR-044-json-store-abstraction.md)
- [DR-040 · 定义质量评分(属加种差/非循环/反例 + 参考重叠;LLM 1.0 vs 数据驱动 0.0)](decisions/DR-040-definition-quality-eval.md)
- [DR-039 · 反幻觉评测台(带标签对抗基准 + 泄漏率;反幻觉变数字)](decisions/DR-039-hallucination-eval-harness.md)
- [DR-038 · 基数自适应 θ(基准纯增益但真实数据回归 → 落地原语不接入)](decisions/DR-038-cardinality-adaptive-threshold.md)
- [DR-037 · 方向感知裁决;数据实证否决朴素强门槛翻转(fk_direction/should_reverse)](decisions/DR-037-direction-aware-adjudication.md)
- [DR-036 · 自引用与角色键发现(role_targets + 角色感知 name_ok + health 自反豁免)](decisions/DR-036-self-referential-and-role-keys.md)
- [DR-035 · 单一裁决核 dao_core(消两份漂移实现 + classify 三态 + 引擎平价)](decisions/DR-035-unified-adjudication-core.md)
- [DR-049 · 语义层关系型投影(OWL 标准出口 + 关系表消费出口)+ 深度问数链路显式编排](decisions/DR-049-relational-projection-and-explicit-pipeline.md)
- [DR-048 · HTTP 慢速攻击缓解(请求头总时限 + 正文最低速率 + 并发上限)](decisions/DR-048-slow-http-dos-mitigation.md)
- [DR-047 · 侧边栏信息架构与字号层级(本体页归一 + 动作中心独立 + 内容字号不倒挂)](decisions/DR-047-sidebar-ia-and-type-scale.md)
- [DR-046 · 确定性模块的隔离单测(离线秒级 + quick_build 可测化)](decisions/DR-046-deterministic-module-unit-tests.md)
- [DR-045 · 工程校验与 TDD 底座(pytest/coverage/ruff/CI + 文档计数自检)](decisions/DR-045-engineering-harness-and-tdd.md)
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
- [IR-001 · DataMind 基座(目录/图谱/指标/质量/SQL)](iterations/IR-001-foundation.md) · **delivered**
- [IR-002 · 深度问数对齐平台 chat-bi](iterations/IR-002-deep-qa-chatbi.md) · **delivered**
- [IR-003 · 本体构建问询台(LLM 辅助建模；二进制附件不在本仓解析)](iterations/IR-003-ontology-build-console.md) · **delivered**
- [IR-004 · 数据连接 + 数据可视化模块](iterations/IR-004-connection-and-viz.md) · **delivered**
- [IR-005 · 安全加固 + 工业化 + 复审收敛](iterations/IR-005-hardening-and-review.md) · **delivered(持续)**
- [IR-006 · IOF/BFO 语义工程层(注释/映射/导出/元数据覆盖)](iterations/IR-006-iof-bfo-semantic-layer.md) · **delivered**
- [IR-007 · 工程校验与 TDD 底座(pytest/coverage/ruff/CI + 确定性模块单测)](iterations/IR-007-tdd-foundation.md) · **in-progress**
- [IR-008 · 裁决核收敛与算法强化(dao_core 单一事实源 + 引擎平价 + 零回归)](iterations/IR-008-adjudication-core-convergence.md) · **in-progress**
- [IR-011 · 单体路由蓝图化(共享上下文基座 + 逐簇拆 blueprint)](iterations/IR-011-blueprint-modularization.md) · **in-progress**
- [IR-009 · 反幻觉评测台与门槛量化(对抗基准 + 泄漏率 + DR-038 回归实测)](iterations/IR-009-eval-and-definition-quality.md) · **in-progress**

## 上游素材(真相来源)
- `../上游本体引擎/` — 引擎(agent_runtime/export_owl/serve_claw)、技能(skills_seed)、方法论(其 `系统设计.md` 与 `specs/`)。DataMind 复用其 engine 与技能。
- `DATAMIND_DB` — 实际只读数据源；仓库不默认附带 `demo_metrics.db`，可用 `examples/sample_db.sql` 创建最小示例库。
- `README.md` — 模块能力总览与实测部署命令。
- `ARCHITECTURE.md` — 分层/数据流/统一约定/安全模型/已知边界(架构说明)
- `AUDIT.md` — 逐轮自测/复审记录(IR 的原始日志,已归纳进 iterations/)。
- `test_all.py` — 系统级回归，源码定义 552 个检查点。
- `SPEC.md` — 对**平台** web/ 的逆向规格(平台契约,非 DataMind;前缀不同勿混用)。

## 现状校准
- 计数(2026-08-28):**125 路由**(server.py 120 + bp_engine 5)/ **552 集成断言** /
  **317 项单元测试** / DR-001…DR-051 · IR-001…IR-011。
  新增共享层 `srv_context`/`srv_engine`、单一裁决核 `dao_core`、评测台 `hallucination_eval`/`definition_eval`、
  持久化抽象 `store`、首个 blueprint `bp_engine`。

- 计数(2026-07-17):27 页面模块 / 82 后端路由 / test_all.py 109 断言 / 数据 108 表 186,833 行 / 107 指标 / 655 术语。
- 计数(2026-07-27):28 页面模块 / 109 后端路由 / test_all.py 249 断言(数据与指标/术语计数不变)。
  - 新增(DR-010/IR-006，历史页面名):`元数据覆盖` 页面模块 + 4 路由。该旧指标只统计元数据字段覆盖，不能证明本体完整或 IOF 合规；当前复核为 60 条关系中 21 条可映射至类型兼容的官方 BFO/IOF 属性，其余 39 条保留在本地命名空间。
- 新增(DR-013):`本体评审` 页面模块 + `/api/ont/review`;关系人审算子 confirm_relation/reject_relation 并入 apply 白名单;关系类算子两种 IR 形状(links/relations)通吃——构建产物(built_*)自此可人审可编辑。test_all.py 119 断言全过。
- 新增(DR-014):问数「沿本体关系召回」步骤+⋈ JOIN 提示;`/api/chat/feedback(+/resolve)` 反馈回流→评审页「本体迭代候选队列」;「根因诊断」模式+`/api/diagnose/stream`(意图→关系召回→边界内约束生成)。
- 新增(DR-015):「动作中心」页 + /api/actions·invoke·log·approve —— 类型化参数/低风险直接形成动作记录/高风险审批/决策记录审计；当前 `decision_capture` 不写回业务系统。已登记动作会投影为本体 action 节点，图谱对象卡显示配置绑定，诊断清单可发起派工记录。
- 新增(DR-016):`mcp_action_server.py` —— 动作层 MCP server(stdio,零依赖):list_actions / **invoke_action(唯一写)** / get_action_status;审批不暴露,治理单一实现留在 HTTP API,Agent 接入自动继承「只能提议、不能批准」。
- 新增(DR-017):「引擎设置」页 + /api/engine/config·test —— 运行时/模型/API Key 实时切换(调用时读 env+清缓存,免重启);Key 只写不回显、0600、清除同步弹 env;先测后切(真实延迟/真实报错)。
- 新增(DR-018):全部读方统一 `load_ir_edited`(单一当前真相)——修复 问数JOIN提示/总览/指标/表详情/graphs列表 读原始IR 的不一致;U 分区 18 断言锁定「写→图谱/总览/问数/SPARQL/评审/列表 即读→撤销复原」。套件 186 断言。
- 新增(DR-019):深度问数十项升级 —— A1 术语词典进检索(term_expand 步)/ A2 SQL 口径拦截 `_validate_sql_ontology`(ontology_gate 步:表白名单+JOIN 键落本体关系)/ A3 指标口径卡(done.metric_cards)/ B4 按任务选模(task_models{plan/narrative/diagnose}+模型族保护)/ B5 多轮指代 `_carryover` / B6 叙述流式 narrative_delta / C7 外部库实连(pymysql;凭据 conn_secrets.json 0600 只写)/ C8 API 型数据源(api_fetch→up.api_* 物化)/ C9 问数评测(benchmark/qa_set.json 8 题参考集×三组,/api/eval/*,「问数评测」页)/ C10「业务助手」页(角色组装问数/诊断/动作/待办)。套件 212 断言。
- 新增(DR-020):动作层产品化 —— 动作类型管理 CRUD(/api/action/type·update·delete,内置种子受保护、停用即刻拦截发起)/ 3 个行业动作种子(冻结批次·温区调参·供应商 SCAR,均绑真实库表)/ 问数答案卡「相关动作」直达发起(SQL 命中表 × object_table 匹配)/ 动作中心 KPI+类型编辑弹窗+审计筛选与 CSV 导出;MCP 读同一注册表零改动继承。套件 226 断言。
- 新增(DR-021):构建技能管理 —— 浏览(内置附目录清单)/在线新建/编辑/两击删除(/api/build/skill/<name>·save·delete);**自定义技能正文注入构建方法论**(_skill_method_text,剥 front-matter,编辑后下次构建即生效)——修复「能上传但从未被消费」的死代码问题;内置技能可改写与隐藏(DR-051:workdir 覆盖层+删除墓碑,出厂正文保留,可恢复默认)。套件 236 断言(105 路由)。
- 新增(DR-022):技能生态五项 —— ①技能对比实验(/api/build/skill_compare·status·results:同目标×两组技能各跑真实构建,对比 对象/关系/verified/动词/类型/定义覆盖;组失败如实展示不充数)②构建流水线 skill_inject 注入痕迹步 ③产物沉淀为技能(/api/build/skill/from_graph:动词表/类型分布/定义样例,确定性提取)④评测败题通用修复(⋈⋈ 两跳路径召回 + 均值分母/单值聚合口径规范进 prompt)⑤存而不用审计(specs/audit-input-consumers.md;第 3 起死代码 qa_skills 已接上:_match_qa_skill 命中复用+skill_reuse 步)。套件 249 断言(109 路由;终跑 C 组 8/8)。
- 引擎在线依赖:深度问数与本体构建的 LLM 步骤依赖 `agent_runtime` 引擎在线;引擎限流(429)/超时时自动兜底(深度问数走模板、构建走 quick_build),结果仍产出并如实标注。
- 已知边界详见 `README.md` 末节(SPARQL 软超时、运行时切换 UI、G6/ECharts 本地内置等)。

## 如何让 AI 代理消费本规约
```
Consult @specs/map.md to find relevant context.
记录一个决定: 总结讨论, 按 @specs/meta.md 添加一个 DR。
实现一个迭代: 按 @specs/meta.md 写 IR(Goal/Deliverables/Tasks/Acceptance), 每任务一次提交, 读码+跑 test_all.py 验收后打勾。
```

## 开源收敛(2026-08-01)
- 本仓开源范围仅 Cosmo DataMind 本体。移除旧系统内嵌:/platform 静态代理、/api/claw 代理、经典部署自拉起、引擎工具箱(TOOLS)、平台成果目录;UI 摘除 本体对话/建模工作台/成果库/平台工作台(原版)/平台对话(原版) 五页。
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
- **DR-026 双盲与使用度**:`intent_check.py`(接入问数两分支,口径校验后执行前)、
  `usage_stat.py` + `GET /api/ont/usage/<key>`。对齐报告电力案例的「双盲检测机制」
  与阶段六的「业务调用频次驱动迭代」。两通道互不透传(A 只看问句、B 只看 SQL),
  均不调 LLM;只观测不阻断。实测抓到真阳性:问「业务员维度表有多少人」而 SQL
  查销售订单去重——口径校验放行,双盲判 mismatch。套件 326 断言。
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
  经 GLM coding plan 实测问数与构建全链路)+ 回归沙箱 `built_regress`
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
- **DR-033 选中本体作锚定源**:`_anchor_ir()` 让选中的图谱真正成为锚定/口径/埋点的本体;
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
  套件 552 条静态断言(有引擎环境实际执行 546/546,无引擎 538 通过/8 条件跳过) · UI 走查 64/64 · UI 实操 45/45，
  另有 3 项因未配置 OpenAI 兼容端点而按条件跳过。
- **快速入门可跑通**(QUICKSTART.md):示例库 SQL → quick_build → 选本体 → 问出数,
  全程无上游引擎。过程中修出四处断链:垫片不承载 driver 注册(配了 key 也拿不到运行时)、
  规划超时写死 60s(推理型模型必超)、`tables[]` 未归一(自建本体被判无绑表而回退)、
  本体缺 attrs 时上下文无列名(模型只能猜列)。

五问互补:够不够用(CQ)· 通不通(链路)· 对不对得上数据(漂移)·
答的是不是问的(双盲)· 结构健不健康(体检)。

四问互补:CQ 答「够不够用」· 链路答「通不通」· 漂移答「还对不对得上数据」·
双盲答「答的是不是问的」。
