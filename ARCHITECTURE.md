# Cosmo DataMind · 系统架构

> 面向维护者的架构说明。契约细节见 `specs/`(DR-001…DR-011)。本文描述分层、数据流与统一约定。

## 1. 分层

```
┌─────────────────────────────────────────────────────────────┐
│  前端 (ui/index.html, 单页, 原生 JS)                          │
│   32 模块 × hash 路由 · G6 图谱 · ECharts · 统一助手 $/esc/J  │
├─────────────────────────────────────────────────────────────┤
│  HTTP 层 (server.py, Flask, 单端口 8092)                      │
│   before_request CSRF 守卫 · 109 路由 · 统一错误/写守卫         │
├─────────────────────────────────────────────────────────────┤
│  能力层                                                        │
│   构建: _gather_evidence → _llm_extract → _adjudicate_ir      │
│         → _llm_semantic_review → 落盘(三级控制环, DR-011)     │
│   问数: build_context → agent_sql_plan → q() → narrative      │
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

## 3. 统一约定(refactor 后)

| 关注点 | 统一点 | 位置 |
|---|---|---|
| 只读取数 | `ro_connect(path)` — mode=ro,缺库响亮失败 | server.py |
| IR 写端点前奏 | `_open_writable(key)` 图谱必填(缺→400,不默认 imom)+ → (ir, wp, err) | server.py,enrich/reground/maturity 共用 |
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

`test_all.py`(300 集成断言,覆盖每路由 happy+边界+安全 + DR-011 O 节)· pyflakes 零告警 · `specs/test/` 回归 · 构建产物经 SHACL/HermiT 校验 · 完备度记分卡量化可审计程度。

## 6. 已知边界(诚实)

- 复合键裁决为二列(≥3 列 schema 未覆盖);
- 语义评审依赖在线引擎(离线记 skipped);
- 外部库仅登记连接、离线不取数(DR-008);
- 单专家式扩标金标(方法学局限,见论文)。
