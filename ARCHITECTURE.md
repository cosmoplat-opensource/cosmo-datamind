# Cosmo DataMind · 系统架构

> 面向维护者的架构说明。契约细节见 `specs/`(DR-001…DR-033)。本文描述分层、数据流与统一约定。

## 1. 分层

```
┌─────────────────────────────────────────────────────────────┐
│  前端 (ui/index.html, 单页, 原生 JS)                          │
│   26 页 × hash 路由 · G6 图谱 · ECharts · 统一助手 $/esc/J    │
├─────────────────────────────────────────────────────────────┤
│  HTTP 层 (server.py, Flask, 单端口 8092)                      │
│   before_request CSRF 守卫 · 121 路由 · 统一错误/写守卫         │
├─────────────────────────────────────────────────────────────┤
│  能力层                                                        │
│   构建: _gather_evidence → _llm_extract → _adjudicate_ir      │
│         → _llm_semantic_review → 落盘(三级控制环, DR-011)     │
│   问数: _anchor_ir(选中图谱=锚定源) → build_context(带轨迹)   │
│         → agent_sql_plan → 口径门禁 → q() → 双盲意图 → 叙事    │
│   体检: cq_check 能力核验 · drift_check 漂移 · health_check    │
│         图结构 · compat_check 兼容 · module_split 模块化       │
│   决策: rule_engine 规则+确定性推理 · 动作层(类型化+风险分级)│
│   导出: _ir_to_turtle → SHACL/HermiT (DR-010)                 │
├─────────────────────────────────────────────────────────────┤
│  执行底座                                                      │
│   本地 SQLite 只读 (ro_connect, mode=ro 三重防写, DR-001)     │
│   IR 存储 (workdir/*.json, _atomic_json + _WRITE_LOCK)        │
│   引擎 (agent_runtime → hermes/claude-code, 离线降级)         │
└─────────────────────────────────────────────────────────────┘
```

## 2. 核心数据流

**本体构建(三级控制环, DR-011)**:LLM 提议关系 → **语义评审**(`_llm_semantic_review`,仅凭 schema 判语义,拦共享域巧合)→ **数据裁决**(`_adjudicate_ir`,取值重叠∧父键唯一,拦标识符错位幻觉)→ 每条关系带 `status`(verified/candidate)+ `semantic`(pass/fail/skipped)+ `founded_relation` + `temporal`。两级过滤移除不相交的假正例族,互补而非替代。引擎离线全程降级、绝不臆造。

**关系发现的泛化**(DR-011,基准实证驱动):连接键多候选循环(后缀词干→等值列名==父表名→前缀 Country1→Country→同名键形列);父列候选序 PK 优先;复合键二列联合裁决(元组重叠∧成对唯一)。覆盖企业 `*_id` 规范库、自然键学术库(Mondial 级)、无约束上传 CSV、复合键 schema。

## 2.5 模块清单

`server.py` 之外的能力模块,均为**确定性计算、不调 LLM**,可独立单测:

| 模块 | 职责 | DR |
|---|---|---|
| `quick_build.py` | 纯数据驱动建本体(取值重叠 ∧ 键名词根闸) | DR-011 |
| `cq_check.py` | 能力核验(CQ):本体够不够回答业务问题;穿透链路是否贯通 | DR-024/025 |
| `drift_check.py` | 概念漂移:本体还对不对得上数据源(表/列/主键/关系四类) | DR-025 |
| `intent_check.py` | 双盲意图检测:问句通道 vs SQL 通道各自锚定,比对是否答非所问 | DR-026 |
| `usage_stat.py` | 本体使用度埋点(只读旁路,不记录问句原文) | DR-026 |
| `rule_engine.py` | 业务规则 + 确定性推理,每条结论带 trace;冲突只报不裁 | DR-028 |
| `health_check.py` | 图结构健康度:悬空/自反/状态矛盾(硬错误)+ 孤岛/枢纽/重复边(信号) | DR-030 |
| `compat_check.py` | 向后兼容:结构 diff + **下游影响**(命中哪些规则/动作/技能) | DR-031 |
| `module_split.py` | 模块化建议(按领域连通分量 / 按数仓分层),只建议不落盘 | DR-031 |
| `openai_runtime.py` | OpenAI 兼容驱动(GLM/DeepSeek/Qwen/vLLM),空内容判失败不回传空串 | DR-029 |
| `mcp_action_server.py` | 对外 MCP:发起动作是唯一写工具,审批不开放 | DR-015 |
| `translate_cn.py` | 术语中文化(离线词典,无网络依赖) | — |

## 3. 统一约定(refactor 后)

| 关注点 | 统一点 | 位置 |
|---|---|---|
| 只读取数 | `ro_connect(path)` — mode=ro,缺库响亮失败 | server.py |
| IR 写端点前奏 | `_open_writable(key)` 图谱必填(缺→400,不默认 demo)+ → (ir, wp, err) | server.py,enrich/reground/maturity 共用 |
| 原子写 | `_atomic_json` + `_WRITE_LOCK` 串行化 | 全部持久化 |
| 路径守卫 | `_bad_gkey` / `_ir_write_path`(拒只读源与穿越) | 全部图谱键入口 |
| 动词接地 | `_FOUNDED_RELATIONS` + `_ground_verb`(BFO + 时间指标) | 单一映射源 |
| kind→BFO | `_KIND_BFO`(含 ice=InformationContentEntity) | 单一映射源 |
| 数据源连接 | `build_connect` 按 path/dsn 幂等登记,去重复堆叠 | server.py |
| 图谱选择器 | `graphOptions(gs,label)` 分组 optgroup(精选/场景/构建) | ui,图谱/工作台/完备度三处共用 |
| 前端 kind 规范 | `KIND{c,n}` 颜色+中文,全站图例/配色/标签引用 | ui,`KCOL/KNM` |
| 前端助手 | `$`/`esc`/`jsAttr`/`J` 显式挂 window | ui,防内联处理器作用域隐患 |
| 嵌入页高度 | 全局 `iframe{height:70vh}` 默认 + 内联 `calc(100vh-178px)` 为准(去 `!important` 覆盖) | ui,3 处嵌入页 |
| 数据格子显示 | `cell(v)` 去浮点表示噪声(6 处预览/结果共用) | ui,显示层不改原始/导出 |
| 前端 XSS | 一切用户/LLM 文本经 `esc()` 入 innerHTML(DR-006) | 全站 |

## 4. 安全模型(DR-006)

只读 SQL 三重防写(mode=ro + `sql_is_readonly` + 单句 execute)· 全局 CSRF 守卫(非安全方法带跨源 Origin→403)· 图谱键防穿越 · 命令白名单(无 shell=True)· SPARQL 禁 SERVICE/外部 FROM · 三写端点经 `_open_writable` 仅回写受控 IR · `_atomic_json` 原子性。

## 5. 测试与质量门

`test_all.py`(535 集成断言,覆盖每路由 happy+边界+安全)· `test_ui.py`(62,全页走查)·
`test_ui_ops.py`(46,真实浏览器逐步实操:切引擎/建本体/对话改本体/审计/问数/选本体锚定)·
pyflakes 零告警 · 构建产物经 SHACL/HermiT 校验 · 完备度记分卡量化可审计程度。

## 6. 已知边界(诚实)

- 复合键裁决为二列(≥3 列 schema 未覆盖);
- 语义评审依赖在线引擎(离线记 skipped);
- 外部库仅登记连接、离线不取数(DR-008);
- 单专家式扩标金标(方法学局限,见论文)。
