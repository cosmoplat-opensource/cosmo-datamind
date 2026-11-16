# COSMO DataMind · 系统架构

> 面向维护者的架构说明。各项设计的细节见 `specs/` 下的决策记录(DR)和迭代记录(IR)。本文描述分层、数据流和统一约定。

## 1. 分层

```
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│  前端 (ui/index.html + ui/modules + ui/styles, 原生 JS/CSS)                                 │
│   18 页 × hash 路由 · G6 图谱 · ECharts · 统一助手 $/esc/J                                  │
├─────────────────────────────────────────────────────────────────────────────────────────────┤
│  HTTP 层 (Flask, 单端口 8092) · 共 144 路由                                                 │
│   server.py 117 + bp_engine 5 + bp_actions 8 + bp_metrics 3 + bp_semantic 2 + bp_decision 9 │
│   生产入口:wsgi.py → Gunicorn(gthread);可另配 Nginx 反向代理                                │
│   app 级 before_request CSRF 校验(对 blueprint 同样生效)                                    │
│   共享层: srv_context(路径/只读连接/原子写/写锁)                                            │
│           srv_engine(运行时缓存/引擎配置/引擎回复语义)                                      │
│           srv_actions(动作存储/参数 schema)                                                 │
├─────────────────────────────────────────────────────────────────────────────────────────────┤
│  能力层                                                                                     │
│   构建: CQ/行业与标准/技能→来源整理→LLM提议→语义复核                                        │
│         → dao_core数据验证→参照注释→指标反解/核验→build_quality验收→人工审核                │
│   问数: _anchor_ir(选中图谱=锚定源) → build_context(带轨迹)                                 │
│         → agent_sql_plan → 本体约束校验(表/JOIN 键/扇出) → q() → 双盲意图 → 叙事            │
│   检查: cq_check 验收问题 · drift_check 漂移 · health_check                                 │
│         图结构 · compat_check 兼容 · module_split 模块化                                    │
│   决策: rule_engine 规则+确定性推理 · 动作层(类型化+风险分级)                               │
│   导出: _ir_to_turtle → RDF 解析 + SHACL 校验 (DR-010)                                      │
├─────────────────────────────────────────────────────────────────────────────────────────────┤
│  执行层                                                                                     │
│   本地 SQLite 只读 (ro_connect, mode=ro 三重防写, DR-001)                                   │
│   IR 存储 (workdir/*.json, _atomic_json + _WRITE_LOCK)                                      │
│   引擎 (agent_runtime → hermes/claude-code, 离线降级)                                       │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
```

## 2. 核心数据流

**本体构建(DR-011/050/052)**：CQ、行业/本体标准配置与技能正文进入构建上下文 → `standard_assets` 从固定版本本地 RDF/XSD 加载所选标准并留下内容指纹 → LLM 只提议关系和候选键 → `_llm_semantic_review` 标记语义存疑项 → `_adjudicate_ir` 调用 `dao_core`，按固定阈值、父键唯一性、命名依据和方向检查验证连接关系 → `build_references.apply_profile` 对整图施加行业/标准候选注释（选择 BFO/IOF 时再由 `ontology_grounding` 按官方 IRI、定义域和值域检查映射）→ `build_quality` 检查图结构、verified 证据、定义、参照缺口和 CQ → IR、manifest/history 与 gaps 原子写入。软参照缺口进入 review，强约束缺口进入 fail；缺少证据的参照项不生成对象。离线路径 `quick_build` 使用同一后处理和验收检查。执行顺序见 `docs/pipelines/ontology_build.yaml`。

**关系发现**(DR-011):连接键多候选循环(后缀词干→等值列名==父表名→前缀 Country1→Country→同名键形列);父列候选序 PK 优先;复合键按元组联合计算重叠率和唯一性，模型给出的多列提示不限于二列，自动组合搜索控制在 2--4 列。针对的情形包括按 `*_id` 命名的企业库、使用自然键的库、没有约束声明的上传 CSV 和复合键。

## 2.5 模块清单

`server.py` 之外的能力模块。除标注外均为**确定性计算、不调 LLM**,可独立单测
(`tests/unit/` 覆盖其边界与纯函数):

| 模块 | 职责 | DR |
|---|---|---|
| `sqlite_evidence.py` | 完整单列/元组集合的 SQL 聚合取证，记录匹配数、去重数与全表唯一性；调用者负责快照和超时 | DR-056 |
| `dao_core.py` | 关系验证的集中实现:重叠/唯一度/命名校验/角色键/方向测试/自适应 θ,三态 `classify` | DR-035…038 |
| `quick_build.py` | 只依据数据建本体,关系验证调用 `dao_core`(compat 规则) | DR-011/035 |
| `hallucination_eval.py` | 错误关系控制评测:在带标签的基准上计算精确率、召回率和错误关系漏出率(可选用模型评判) | DR-039 |
| `definition_eval.py` | 定义质量评分:属加种差/非循环/反例 + 参考重叠(LLM-judge 可选) | DR-040 |
| `build_quality.py` | 确定性验收检查：图结构 + verified 证据契约 + 定义 + 上层关系映射 + CQ，输出 pass/review/fail | DR-050 |
| `build_references.py` | 行业/本体标准目录、配置归一、提示片段、整图候选注释与参照/强约束质量报告 | DR-052 |
| `standard_assets.py` | 固定版本本地标准 RDF/XSD 的清单、离线解析、内容指纹与提示上下文；资产在 `ontology/standards/` | DR-052 |
| `ontology_grounding.py` | BFO/IOF 官方关系 IRI、定义域和值域检查；无法确认时保留本地对象属性 | DR-010/050 |
| `skill_registry.py` | 本仓/上游技能合并发现,列表/查看/执行/prompt 正文消费的单一注册表;`front_matter` 是技能元数据的唯一解析器(摘要只从 `---` 围栏内取) | DR-050/053 |
| `store.py` | JSON 持久化抽象:原子写/坏档恢复/schema 校验/迁移/每路径锁 | DR-044 |
| `ir_relational.py` | IR→关系型语义层投影(只读派生物;OWL 之外的消费出口,证据随行、状态不提升) | DR-049 |
| `srv_context.py` | 共享上下文:路径、`ro_connect`、`sql_is_readonly`、`_atomic_json`、写锁、`confine` 路径限定 | DR-043 |
| `database_scope.py` | 已登记 SQLite 来源解析、本体来源路径匹配；请求级数据库上下文由 server.py 管理 | DR-095 |
| `ir_shape.py` | IR 形状兼容规则集中定义:`rels`(links/relations 双形状)、`obj_names`(对象可指代名超集)与 `strip_cn_suffix/name_variants`(中文表名后缀剥离,指代延续/根因诊断/画像检索共用) | DR-035/055 |
| `key_links.py` | 按键名补提关系候选:跨轮连接已有对象、关联表、多态引用;只提候选,是否成立由数据验证决定 | DR-069 |
| `build_prep.py` | 构建前检查:数据源概况、建模规律识别(无外键/软删除/多态引用/审计列/分表)、资料覆盖与漂移、能源/碳排放参照建议、枢纽优先的分轮计划;只读不调模型 | DR-060 |
| `ext_sample.py` | 外部 MySQL/Doris 只读抽样到本地 SQLite:语句白名单 + 只读会话 + 超时;保留表/字段注释(`_dm_*`),多库合并加前缀 | DR-060 |
| `doc_chunks.py` | 长文证据分块与检索:按标题结构分块(带出处),先按本轮表名锚定、再按诉求 BM25 检索,受文档预算约束 | DR-059 |
| `content_quality.py` | 生成内容的确定性净化:信息载体误判纠正(ice 优先)与正例证据可定位性检查,只读不改写历史 | DR-052 |
| `srv_engine.py` | 引擎共享层:运行时缓存、引擎配置读写/应用、引擎回复语义 | DR-043/017 |
| `bp_engine.py` | 引擎设置 blueprint(5 路由;仅路由,共享态在 `srv_engine`) | DR-043 |
| `srv_actions.py` | 动作共享层:注册表/日志读取、参数 schema 校验 | DR-043/020 |
| `bp_actions.py` | 动作类型/发起/审批 blueprint(8 路由;目录函数由主应用注入) | DR-043/020 |
| `cq_check.py` | 验收问题(CQ)核验:本体能否回答业务问题;跨对象的关系路径是否连通 | DR-024/025 |
| `drift_check.py` | 概念漂移:本体与数据源是否仍然一致(表/列/主键/关系四类) | DR-025 |
| `intent_check.py` | 双盲意图检测:问句通道 vs SQL 通道各自定位对象,比对回答是否偏离问题 | DR-026 |
| `usage_stat.py` | 本体使用度埋点(只读旁路,不记录问句原文) | DR-026 |
| `rule_engine.py` | 业务规则 + 确定性推理,每条结论带 trace;冲突只报告,不自动裁定 | DR-028 |
| `health_check.py` | 图结构检查：悬空/自反/状态矛盾(阻断问题)+ 孤岛/枢纽/重复边(待复核信号) | DR-030 |
| `compat_check.py` | 向后兼容:结构 diff + **下游影响**(命中哪些规则/动作/技能) | DR-031 |
| `module_split.py` | 模块化建议(按领域连通分量 / 按数仓分层),只建议不落盘 | DR-031 |
| `openai_runtime.py` | OpenAI 兼容驱动(GLM/DeepSeek/Qwen/vLLM),空内容判失败不回传空串 | DR-029 |
| `mcp_action_server.py` | 对外 MCP:发起动作是唯一写工具,审批不开放;另有只读语义工具(概念检索/指标计算规则/按 certified 指标取数);协议版本按规范协商,工具带 `annotations` 行为提示 | DR-015/053/055 |
| `mcp_build_server.py` | 对外 MCP(构建):发起构建是唯一写工具,verified/asserted/certified 与删除不开放;start_build 立即返回作业 id,SSE 必须有 done 才算完成(DR-056 同规);验收复跑与人工审核队列只读 | DR-058/016 |
| `llm_json.py` | LLM 回复 JSON 抽取的集中定义:字符串感知配平扫描+最小修复(尾逗号/非有限值),本体提议形状契约与剔除计数 | DR-058 |
| `metric_contract.py` | **指标契约**:确定性编译 SQL、目录核对、只读执行、与参照比对;`verified` 只来自「可执行∧与参照一致」,`certified` 只能由人工确认 | DR-054 |
| `metric_mining.py` | 指标反解:历史 SQL / 指标定义表 / 已保存的 SQL / 参考基准 → 带来源的候选与参照;含 JOIN 不猜计算规则 | DR-054 |
| `metric_pipeline.py` | 反解→归一→核验→并入 IR 的编排;`readjudicate` 复用已存参照重跑 | DR-054 |
| `ontology_decision.py` | 工艺路线层与决策层:工艺路线、约束(硬/软、单位、目的、例外、依据)、点亮矩阵、审计;路线由验收检查按方向核验 | DR-117 |
| `qa_decision.py` | 问数使用决策层:约束与规则按锚定对象带入、未接入属性拦截、业务词解析、问题分型;判定、解释、追踪、异常汇总的确定性求解;推演(调用配置了执行器的动作);固定词表认不出时由模型在图谱声明的选项里选;依据卡 | DR-119/120/123 |
| `answer_record.py` | 答案的结果记录:结果编号、图谱与发布状态;数值匹配:文字说明里的数字到查询结果里找对应的数值,看符号、单位、所属行和列名,对不上的提示以表格为准 | DR-122、DR-128、DR-129 |
| `qa_query_status.py` | 查数结果的状态:取到数据、空、失败三种分开;空结果核对所读表的行数;每个结果给出实际取数截至的日期 | DR-130 |
| `sql_structure.py` | SELECT 语句的结构解析:按作用域取出表、别名、连接条件(含 USING / NATURAL JOIN)和聚合调用,供本体约束校验使用;只解析不求值,解析不了时由调用方退回正则做法 | DR-133 |
| `qa_eval_stats.py` | 问数评测的统一比对规则(结果集严格一致与列容忍两种口径)、按题配对的 95% 区间、候选上限统计 | DR-134 |
| `arithmetic_witness.py` | 算术见证:同一行里一列等于另外两列的和、差、积、商,逐行成立时记为证据;核验派生公式,问数时提示模型 | DR-135 |
| `header_parse.py` | 上传 CSV 的多级表头:识别 2~3 行表头并展开为一行列名;上层是期间时另给长表 | DR-135 |
| `data_grounding.py` | 名称不透明库的数据接地:取值画像、口径说明里的用语 → 列(数据核验)、按取值反查、按画像起候选名(理由须在数据里找到) | DR-136 |
| `feedback_replay.py` | 反馈的回放验证:反馈时保存答案的数据摘要,回放后比对数据是否一致 | DR-137 |
| `ontology_release.py` | 发布状态判定:已发布 / 草案 / 不可用于正式问数 | DR-115 |
| `bp_decision.py` | 工艺路线层与决策层 blueprint(9 路由:约束、路线、点亮矩阵、审计) | DR-117 |
| `bp_metrics.py` | 指标契约 blueprint(3 路由:契约/维度/人工确认计算规则/重跑核验);不提供直接置 verified 的途径 | DR-054 |
| `concept_profile.py` | 概念画像:对象的名称/定义/属性/强关系邻居/指标确定性聚合 + 可回放关键词检索(名称按 `ir_shape` 的中文表名后缀变体匹配) | DR-055 |
| `osi_export.py` | Apache Ossie(原 OSI)语义模型与本体 YAML 出口:形状按 `ontology/standards/ossie/` 快照的官方 schema,状态与证据走 `custom_extensions`;自带确定性发射器,`validate()` 用官方 schema 校验 | DR-055 |
| `bp_semantic.py` | 画像检索与 OSI 导出 blueprint(2 路由) | DR-055 |
| `translate_cn.py` | 术语中文化(离线词典,无网络依赖) | — |
| `action_ontology.py` | 把已登记的动作类型投影为本体 IR 中的节点;只表示系统里登记了这个动作及其对象/表绑定 | DR-015/050 |
| `build_cleaning.py` | 交叉复核与清洗规则的接入:构建后运行复核技能,问数时把清洗规则变成只读连接上的临时视图,不改源库 | DR-063 |
| `code_contracts.py` | 从上传的代码与文档读出连接契约和码表契约(SQL 的 JOIN ON、按字典编码取值等);只读,不执行代码 | DR-065 |
| `concept_hints.py` | 概念层线索:表族(多张表是同一概念)、类别子集(一张表装多种概念)、软删除与版本重复两类有效行规律;确定性识别 | DR-064 |
| `data_catalog.py` | 库内字段文档与名称—取值型数据目录的只读读取 | DR-093 |
| `declared_fk.py` | SQLite 声明外键的取证,模型构建与离线构建共用 | DR-093 |
| `workload_concepts.py` | 由业务问题提出概念、绑定和取数建议,并用可回放的数据检查核对;结果保持候选状态 | DR-093 |
| `relation_triage.py` | 候选关系分流:依据已记录的验证证据分为自动排除、暂缓和待审,待审项按证据强弱排序 | DR-073 |
| `ontology_domain.py` | 有效行规则的集中定义,验收与问数共用 `specs/models/valid_rows_rules.json` | DR-066 |
| `observability.py` | 构建与问数的结构化追踪和指标;问句、SQL 等敏感内容只记指纹 | DR-066 |
| `srv_hardening.py` | 开发服务器的慢速请求缓解:请求头时限、正文最低速率、并发上限 | DR-048 |

## 3. 统一约定(refactor 后)

| 关注点 | 统一点 | 位置 |
|---|---|---|
| 只读取数 | `ro_connect(path)` — mode=ro,缺库显式报错 | server.py |
| IR 写端点前奏 | `_open_writable(key)` 图谱必填(缺→400,不默认 demo)+ → (ir, wp, err) | server.py,enrich/reground/maturity 共用 |
| 原子写 | `_atomic_json` + `_WRITE_LOCK` 串行化 | 全部持久化 |
| 路径校验 | `_bad_gkey` / `_ir_write_path`(拒绝只读源与路径穿越) | 全部图谱键入口 |
| 上层关系映射 | `ontology_grounding.normalize`(官方 IRI + 定义域/值域检查) | `ontology_grounding.py` |
| 构建参照配置 | `build_references.normalize/apply_profile/evaluate`；显式 none 不回填 BFO/IOF | `build_references.py` |
| 本地标准资产 | `standard_assets.status/prompt_context/trace`；只读清单文件，不跟随 import 联网 | `standard_assets.py`, `ontology/standards/` |
| kind→上层类别 | `KIND_DEFAULTS`(含 ice=InformationContentEntity) | `ontology_grounding.py` |
| 数据源连接 | `build_connect` 按 path/dsn 幂等登记,去重复堆叠 | server.py |
| 数据库切换 | `X-DataMind-Source` 解析注册来源；本体按物理来源匹配；统计缓存和来源相关记录分开；前端 `database-scope.js` 管理选择、请求与草稿 | DR-095 |
| 图谱选择器 | `graphOptions(gs,label)` 分组 optgroup(精选/场景/构建) | ui,图谱/工作台/元数据覆盖三处共用 |
| 指标状态呈现 | `MST{状态→[tag 类,中文]}` + `mstTag()`,指标中心/计算规则卡/即时问数共用;人工操作只有业务确认/停用/撤回 | ui,DR-054 |
| 前端 kind 规范 | `KIND{c,n}` 颜色+中文,全站图例/配色/标签引用 | ui,`KCOL/KNM` |
| 关系状态边样式 | `EST{c,w,d,n}` 集中定义(G6 图谱 + 锚定子图 SVG 共用),`estDash()` 转 SVG 虚线 | ui,两处共用一套配色 |
| 前端助手 | `$`/`esc`/`jsAttr`/`J` 显式挂 window | ui,防内联处理器作用域隐患 |
| 嵌入页高度 | 全局 `iframe{height:70vh}` 默认 + 内联 `calc(100vh-178px)` 为准(去 `!important` 覆盖) | ui,3 处嵌入页 |
| 数据格子显示 | `cell(v)` 去浮点表示噪声(6 处预览/结果共用) | ui,显示层不改原始/导出 |
| 技能元数据 | `skill_registry.front_matter` 只认 `---` 围栏内标量键;列表/覆盖件/保存软校验共用一份判定 | `skill_registry.py`,取代此前三处不一致的正则 |
| 读缓存失效 | `_stat_sig(*paths)` 文件指纹(mtime_ns+size,缺文件记 `(p,0,-1)`);不用 TTL,改完即刻生效 | server.py,`/api/graphs` 与表清单 |
| 前端 XSS | 一切用户/LLM 文本经 `esc()` 入 innerHTML(DR-006) | 全站 |

## 4. 安全模型(DR-006)

开发服务器慢速攻击缓解(请求头总时限 + 正文最低速率限制 + 阻塞读看门狗 + 并发上限,DR-048)；生产由 Gunicorn 有界线程/超时与 Nginx header/body/keepalive 超时共同承担连接边界。只读 SQL 三重防写(mode=ro + `sql_is_readonly` + 单句 execute)· 全局 CSRF 校验(非安全方法带跨源 Origin→403)· 图谱键防穿越 · 静态资源 JS/CSS 白名单 · 命令白名单(无 shell=True)· SPARQL 禁 SERVICE/外部 FROM · 三写端点经 `_open_writable` 仅回写受控 IR · `_atomic_json` 原子性。

## 5. 测试与验收检查

**分层验收**(IR-007/014):

| 层 | 内容 | 是否需起服务 |
|---|---|---|
| 单元层 `tests/` | 确定性模块边界/纯函数 + MCP 协议合规 + 缓存失效面 + 文档计数自检 | 否(离线) |
| 集成层 `test_all.py` | 源码定义 548 个检查点,覆盖路由正常路径、边界与安全约束 | 是 |
| UI 层 | `test_ui.py`(全页走查)·`test_ui_ops.py`(浏览器逐步实操) | 是(需 playwright) |

自动化校验:`pyproject.toml` 统一 pytest/coverage/ruff(只启用 F、B 两类规则)/mypy ·
`.pre-commit-config.yaml` 提交即跑 · `.github/workflows/ci.yml` 三类 job(单元、确定性集成、浏览器,均为必过项)·
pyflakes 零告警 · 构建产物经 RDF 解析与 SHACL 校验 · 元数据覆盖率用于定位定义、反例和标准关系映射缺口（不表示本体完备性）。当前仓库不内置 OWL DL 推理器，因此不把 HermiT 一致性检查列为已执行能力。

### 5.1 集成套件的环境依赖

`test_all.py` 源码定义 **548 个检查点**。若出现下列 **5 项**失败,先查环境再查代码——
它们同出一源:`workdir/engine_config.json` 持久化的 `driver` 在当前环境**未注册**
(如 `driver="openai"` 但未配置 OpenAI 兼容端点凭据),编辑引擎据 DR-003/029 显式报错而非伪装可用。

| 失败项 | 说明 |
|---|---|
| `apply rename ok` / `apply 非白名单→400` / `undo ok` / `apply 穿越图谱键→拒` | 编辑算子需可用运行时 |
| `engine config 200` | 断言 `driver ∈ runtimes`,driver 未注册即不成立 |

**排除方法**:把 `driver` 切到 `/api/engine/config` 的 `runtimes` 中已列出的任一运行时后重跑;
5 项应同时通过。切换后仍失败的,才属于回归缺陷。

**另一种情形:全新克隆、完全没有上游引擎。** 此时 `workdir/engine_config.json` 根本不存在
(该文件是本机配置,不随仓分发)。这 6 项**不再计为失败**:套件启动时探测
`/api/engine/config`,引擎不可用时由 `chk_engine` 记为**条件跳过**并逐条列出,
退出码仍为 0,以免 CI 因缺少可选依赖而始终失败。除上述 5 项外
另有 1 项同源,合计 **6 项**(技能中心的两项随 DR-109 去掉):

| 追加失败项 | 说明 |
|---|---|
| `AO5 上游本体未接入当前查询表时不伪装成可问数` | 需上游引擎产出的本体 |

这 6 项是**无引擎环境的预期结果,不是回归**,以 `⊘` 列在「条件跳过清单」里。本仓内置的本体构建技能不受影响——
它由 `skill_registry` 从 `skills_seed/` 读取(DR-050),`/api/build/skills` 在无引擎时
照常返回内置技能,对应的 X1/X6/X8 等检查点应当通过。2026-11-15 在没有上游引擎的隔离环境里
(`scripts/check.py --suite all`)的结果是 **536 通过 / 0 失败 / 6 条件跳过**(退出码 0),跳过项就是上面两表列出的 6 项。
接入上游引擎的环境本轮没有运行。

`test_ui_ops.py` 有 **3 项**同源:`含 OpenAI 兼容端点卡(GLM)` / `切回 GLM 后端生效` /
`GLM「测试连通」出结果`。未配置 OpenAI 兼容端点(`DATAMIND_LLM_BASE`+`DATAMIND_LLM_KEY`)时,
`openai` 运行时按 [[DR-029-openai-compat-runtime-and-test-isolation]] **不注册**(不虚报「LLM 可用」),
引擎设置页因此不显示该卡,相关检查标记为跳过。2026-11-15 的同一次运行没有配置外部端点,
结果是 `test_ui.py` **47 项通过、0 项失败**,`test_ui_ops.py` **48 项通过、0 项失败、3 项跳过**;配置端点后的结果本轮没有运行。

## 6. 已知限制

- 本体约束校验用仓库自带的 SELECT 语句解析器取表、别名和连接条件,不是完整的 SQL 方言解析器;解析不了的语句退回基于正则的近似检查(计数见 `/api/obs/metrics` 的 `qa.sql_parse_fallback`);
- 复合键自动组合搜索限制为 2--4 列；更宽的组合需由调用方给出候选键，系统不做无界穷举;
- 语义评审依赖在线引擎(离线记 skipped);
- MySQL/Doris 直连依赖相应驱动和可达服务；本轮没有可用外部实例，未做真实远端数据库联调;
- 评测用的参考集由单人标注,没有多人交叉标注。

## 7. 完整取证与可复现验收（DR-056）

LLM 构建与 quick_build 共用 `sqlite_evidence.relation_signals`，在完整取值集合上取证，不用 LIMIT 截取的前若干行下验证结论。
联合外键保持约束顺序；声明 FK 的孤儿数据进入 candidate。
指标最新机器证据可降级旧 verified，人工决定受保护；主 SQL 与参照共享读快照，参照可重放、分组维度精确匹配。
图谱范围与显式选表在 SQL 执行前用 SQLite authorizer 检查；离线模板不支持的问题报告为未覆盖。
构建 SSE 必须有 done 才算完成，前端异步选择按当前请求判定，失败可重试。
`python scripts/check.py --suite all` 在临时数据目录运行完整检查，清理服务，保存日志与退出码。
最近一次代码清理、实际部署与 UI 检查见 [2026-10-12 复核报告](docs/audits/2026-10-12-code-cleanup.md)。
此前检查见 [2026-09-07 审计报告](docs/audits/2026-09-07.md)。
