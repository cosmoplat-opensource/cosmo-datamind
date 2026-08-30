# Cosmo DataMind · 系统架构

> 面向维护者的架构说明。契约细节见 `specs/`(DR-001…DR-051、IR-001…IR-011)。本文描述分层、数据流与统一约定。

## 1. 分层

```
┌─────────────────────────────────────────────────────────────┐
│  前端 (ui/index.html, 单页, 原生 JS)                          │
│   26 页 × hash 路由 · G6 图谱 · ECharts · 统一助手 $/esc/J    │
├─────────────────────────────────────────────────────────────┤
│  HTTP 层 (Flask, 单端口 8092) · 共 125 路由                    │
│   server.py 117 路由 + bp_engine.py 5 路由(blueprint, DR-043)│
│   app 级 before_request CSRF 守卫(对 blueprint 同样生效)      │
│   共享层: srv_context(路径/只读连接/原子写/写锁)              │
│           srv_engine(运行时缓存/引擎配置/引擎回复语义)         │
├─────────────────────────────────────────────────────────────┤
│  能力层                                                        │
│   构建: CQ/技能→来源整理→LLM提议→语义复核→dao_core数据验证   │
│         → 上层关系映射检查→build_quality验收检查→人工复核     │
│   问数: _anchor_ir(选中图谱=锚定源) → build_context(带轨迹)   │
│         → agent_sql_plan → 口径校验 → q() → 双盲意图 → 叙事    │
│   体检: cq_check 能力核验 · drift_check 漂移 · health_check    │
│         图结构 · compat_check 兼容 · module_split 模块化       │
│   决策: rule_engine 规则+确定性推理 · 动作层(类型化+风险分级)│
│   导出: _ir_to_turtle → RDF 解析 + SHACL 校验 (DR-010)        │
├─────────────────────────────────────────────────────────────┤
│  执行层                                                      │
│   本地 SQLite 只读 (ro_connect, mode=ro 三重防写, DR-001)     │
│   IR 存储 (workdir/*.json, _atomic_json + _WRITE_LOCK)        │
│   引擎 (agent_runtime → hermes/claude-code, 离线降级)         │
└─────────────────────────────────────────────────────────────┘
```

## 2. 核心数据流

**本体构建(DR-011/050)**：CQ 与技能正文进入提议上下文 → LLM 只提议关系和候选键 → `_llm_semantic_review` 标记语义存疑项 → `_adjudicate_ir` 调用 `dao_core`，按固定阈值、父键唯一性、命名依据和方向检查验证连接关系 → `ontology_grounding` 按官方 IRI、定义域和值域检查 BFO/IOF 映射 → `build_quality` 检查图结构、verified 证据、定义、映射和 CQ → IR 与 gaps 原子写入。每条关系分别记录数据验证状态 `status` 和上层映射状态 `grounding_status`；离线路径 `quick_build` 使用同一数据验证规则和验收检查。执行顺序见 `docs/pipelines/ontology_build.yaml`。

**关系发现的泛化**(DR-011,基准实证驱动):连接键多候选循环(后缀词干→等值列名==父表名→前缀 Country1→Country→同名键形列);父列候选序 PK 优先;复合键按元组联合计算重叠率和唯一性，模型给出的多列提示不限于二列，自动组合搜索控制在 2--4 列。覆盖企业 `*_id` 规范库、自然键学术库(Mondial 级)、无约束上传 CSV、复合键 schema。

## 2.5 模块清单

`server.py` 之外的能力模块。除标注外均为**确定性计算、不调 LLM**,可独立单测
(`tests/unit/` 覆盖其边界与纯函数):

| 模块 | 职责 | DR |
|---|---|---|
| `dao_core.py` | **单一裁决核**:重叠/唯一度/命名校验/角色键/方向测试/自适应 θ,三态 `classify` | DR-035…038 |
| `quick_build.py` | 纯数据驱动建本体,裁决决策委托 `dao_core`(compat 口径) | DR-011/035 |
| `hallucination_eval.py` | 错误关系控制评测台:带标签基准上量化精确率/召回/**幻觉泄漏率**(judge 可选) | DR-039 |
| `definition_eval.py` | 定义质量评分:属加种差/非循环/反例 + 参考重叠(LLM-judge 可选) | DR-040 |
| `build_quality.py` | 确定性验收检查：图结构 + verified 证据契约 + 定义 + 上层关系映射 + CQ，输出 pass/review/fail | DR-050 |
| `ontology_grounding.py` | BFO/IOF 官方关系 IRI、定义域和值域检查；无法确认时保留本地对象属性 | DR-010/050 |
| `skill_registry.py` | 本仓/上游技能合并发现,列表/查看/执行/prompt 正文消费的单一注册表 | DR-050 |
| `store.py` | JSON 持久化抽象:原子写/坏档恢复/schema 校验/迁移/每路径锁 | DR-044 |
| `ir_relational.py` | IR→关系型语义层投影(只读派生物;OWL 之外的消费出口,证据随行、状态不提升) | DR-049 |
| `srv_context.py` | 共享上下文:路径、`ro_connect`、`sql_is_readonly`、`_atomic_json`、写锁 | DR-043 |
| `srv_engine.py` | 引擎共享层:运行时缓存、引擎配置读写/应用、引擎回复语义 | DR-043/017 |
| `bp_engine.py` | 引擎设置 blueprint(5 路由;仅路由,共享态在 `srv_engine`) | DR-043 |
| `cq_check.py` | 能力核验(CQ):本体够不够回答业务问题;穿透链路是否贯通 | DR-024/025 |
| `drift_check.py` | 概念漂移:本体还对不对得上数据源(表/列/主键/关系四类) | DR-025 |
| `intent_check.py` | 双盲意图检测:问句通道 vs SQL 通道各自锚定,比对是否答非所问 | DR-026 |
| `usage_stat.py` | 本体使用度埋点(只读旁路,不记录问句原文) | DR-026 |
| `rule_engine.py` | 业务规则 + 确定性推理,每条结论带 trace;冲突只报不裁 | DR-028 |
| `health_check.py` | 图结构检查：悬空/自反/状态矛盾(阻断问题)+ 孤岛/枢纽/重复边(待复核信号) | DR-030 |
| `compat_check.py` | 向后兼容:结构 diff + **下游影响**(命中哪些规则/动作/技能) | DR-031 |
| `module_split.py` | 模块化建议(按领域连通分量 / 按数仓分层),只建议不落盘 | DR-031 |
| `openai_runtime.py` | OpenAI 兼容驱动(GLM/DeepSeek/Qwen/vLLM),空内容判失败不回传空串 | DR-029 |
| `mcp_action_server.py` | 对外 MCP:发起动作是唯一写工具,审批不开放 | DR-015 |
| `translate_cn.py` | 术语中文化(离线词典,无网络依赖) | — |

## 3. 统一约定(refactor 后)

| 关注点 | 统一点 | 位置 |
|---|---|---|
| 只读取数 | `ro_connect(path)` — mode=ro,缺库显式报错 | server.py |
| IR 写端点前奏 | `_open_writable(key)` 图谱必填(缺→400,不默认 demo)+ → (ir, wp, err) | server.py,enrich/reground/maturity 共用 |
| 原子写 | `_atomic_json` + `_WRITE_LOCK` 串行化 | 全部持久化 |
| 路径守卫 | `_bad_gkey` / `_ir_write_path`(拒只读源与穿越) | 全部图谱键入口 |
| 上层关系映射 | `ontology_grounding.normalize`(官方 IRI + 定义域/值域检查) | `ontology_grounding.py` |
| kind→上层类别 | `KIND_DEFAULTS`(含 ice=InformationContentEntity) | `ontology_grounding.py` |
| 数据源连接 | `build_connect` 按 path/dsn 幂等登记,去重复堆叠 | server.py |
| 图谱选择器 | `graphOptions(gs,label)` 分组 optgroup(精选/场景/构建) | ui,图谱/工作台/元数据覆盖三处共用 |
| 前端 kind 规范 | `KIND{c,n}` 颜色+中文,全站图例/配色/标签引用 | ui,`KCOL/KNM` |
| 关系状态边样式 | `EST{c,w,d,n}` 单一事实源(G6 图谱 + 锚定子图 SVG 共用),`estDash()` 转 SVG 虚线 | ui,消两套配色 |
| 前端助手 | `$`/`esc`/`jsAttr`/`J` 显式挂 window | ui,防内联处理器作用域隐患 |
| 嵌入页高度 | 全局 `iframe{height:70vh}` 默认 + 内联 `calc(100vh-178px)` 为准(去 `!important` 覆盖) | ui,3 处嵌入页 |
| 数据格子显示 | `cell(v)` 去浮点表示噪声(6 处预览/结果共用) | ui,显示层不改原始/导出 |
| 前端 XSS | 一切用户/LLM 文本经 `esc()` 入 innerHTML(DR-006) | 全站 |

## 4. 安全模型(DR-006)

慢速攻击缓解(请求头总时限 + 正文最低速率限制 + 阻塞读看门狗 + 并发上限,DR-048)· 只读 SQL 三重防写(mode=ro + `sql_is_readonly` + 单句 execute)· 全局 CSRF 守卫(非安全方法带跨源 Origin→403)· 图谱键防穿越 · 命令白名单(无 shell=True)· SPARQL 禁 SERVICE/外部 FROM · 三写端点经 `_open_writable` 仅回写受控 IR · `_atomic_json` 原子性。

## 5. 测试与验收检查

**两层分工**(IR-007 起):

| 层 | 内容 | 是否需起服务 |
|---|---|---|
| 单元层 `tests/` | 确定性模块边界/纯函数 + 文档计数自检(2026-08-29:284 项测试) | 否(离线秒级) |
| 集成层 `test_all.py` | 源码定义 553 个检查点,覆盖路由正常路径、边界与安全约束 | 是 |
| UI 层 | `test_ui.py`(全页走查)·`test_ui_ops.py`(浏览器逐步实操) | 是(需 playwright) |

自动化校验:`pyproject.toml` 统一 pytest/coverage/ruff(只选 F/B 抓真缺陷)/mypy ·
`.pre-commit-config.yaml` 提交即跑 · `.github/workflows/ci.yml` 双 job(单元与确定性集成都硬挡)·
pyflakes 零告警 · 构建产物经 RDF 解析与 SHACL 校验 · 元数据覆盖率用于定位定义、反例和标准关系映射缺口（不表示本体完备性）。当前仓库不内置 OWL DL 推理器，因此不把 HermiT 一致性检查列为已执行能力。

### 5.1 集成套件的环境依赖(勿误判为回归)

`test_all.py` 源码定义 **553 个检查点**。若出现下列 **5 项**失败,先查环境再查代码——
它们同出一源:`workdir/engine_config.json` 持久化的 `driver` 在当前环境**未注册**
(如 `driver="openai"` 但未配置 OpenAI 兼容端点凭据),编辑引擎据 DR-003/029 显式报错而非伪装可用。

| 失败项 | 说明 |
|---|---|
| `apply rename ok` / `apply 非白名单→400` / `undo ok` / `apply 穿越图谱键→拒` | 编辑算子需可用运行时 |
| `engine config 200` | 断言 `driver ∈ runtimes`,driver 未注册即不成立 |

**排除方法**:把 `driver` 切到 `/api/engine/config` 的 `runtimes` 中已列出的任一运行时后重跑;
5 项应同时转绿。若切换后仍失败,才是真回归。

**另一种情形:全新克隆、完全没有上游引擎。** 此时 `workdir/engine_config.json` 根本不存在
(该文件是本机配置,不随仓分发)。这 8 项**不再计为失败**:套件启动时探测
`/api/engine/config`,引擎不可用时由 `chk_engine` 记为**条件跳过**并逐条列出,
退出码仍为 0——否则 CI 的强制阻断会因为「缺少可选依赖」而恒红。除上述 5 项外
另有 3 项同源,合计 **8 项**:

| 追加失败项 | 说明 |
|---|---|
| `skills 列表` | 断言 `/api/skills` 至少 5 个,该端点列的是**上游引擎**技能 |
| `skill 详情` | 取上游引擎技能 `gov-app-ontology-build` |
| `AO5 上游引擎本体也能锚定并给出关系` | 需上游引擎产出的本体 |

这 8 项是**无引擎环境的预期结果,不是回归**,以 `⊘` 列在「条件跳过清单」里。本仓内置的本体构建技能不受影响——
它由 `skill_registry` 从 `skills_seed/` 读取(DR-050),`/api/build/skills` 在无引擎时
照常返回内置技能,对应的 X1/X6/X8 等检查点应当通过。实测:随仓演示数据集的干净克隆上
**527 通过 / 0 失败 / 8 条件跳过**(退出码 0),跳过项与上表逐一吻合;
配齐上游引擎的环境为 **535 通过 / 0 失败 / 0 跳过**。

`test_ui_ops.py` 有 **3 项**同源:`含 OpenAI 兼容端点卡(GLM)` / `切回 GLM 后端生效` /
`GLM「测试连通」出结果`。未配置 OpenAI 兼容端点(`DATAMIND_LLM_BASE`+`DATAMIND_LLM_KEY`)时,
`openai` 运行时按 [[DR-029-openai-compat-runtime-and-test-isolation]] **不注册**(不虚报「LLM 可用」),
引擎设置页因此不显示该卡,相关检查应标记为跳过而不是失败。本轮未配置该外部端点，
实测为 **43 项通过、0 项失败、3 项跳过**，不据此推测配齐端点后的结果。

## 6. 已知边界(诚实)

- 复合键自动组合搜索限制为 2--4 列；更宽的组合需由调用方给出候选键，系统不做无界穷举;
- 语义评审依赖在线引擎(离线记 skipped);
- MySQL/Doris 直连依赖相应驱动和可达服务；本轮没有可用外部实例，未做真实远端数据库联调;
- 单专家式扩标参考集(方法学局限,见论文)。
