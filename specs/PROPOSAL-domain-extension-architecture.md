# PROPOSAL · 领域可插拔扩展架构（本体应用 + 契约抽象 + 词表下沉）

> **后续澄清**：本次 yang 深度集成以 [代码复核版技术方案](PROPOSAL-yang-datamind-integration.md) 与 DR-141～DR-147 为准。
> 当前基础版进一步以 [DR-148](decisions/DR-148-minimal-robotics-task-workflow.md) / [IR-020](iterations/IR-020-minimal-robotics-task-workflow.md) 为准：当前任务缺少的参数、映射和适配在 DataMind 内补齐，不依赖外部编辑器。
> 本文保留为早期架构探索，不作为当前直接实施清单；统一载荷、参数状态、运行追溯、UI 范围及模板能力以新方案修订为准。
> 当前方案复用已有对话历史，补充运行与产物关联；manifest 指产物来源清单，与本文的领域插件 manifest 含义不同。

> **状态**：提议（未立项）。**日期**：2026-10-29。
> **消费方式**：`Consult @specs/map.md` 找到本文；本文是**待实施清单**，不是现状说明。
> 现状口径仍以 `README.md`、`ARCHITECTURE.md`、`docs/pipelines/ontology_build.yaml` 为准。
>
> **背景**：为接入「天驭机器人平台」这一外部领域而做的架构设计。天驭侧素材（行为树 XML、节点模型、
> 本体框架图、ROS 接口）在本仓之外，故本文只描述**本仓要改什么**，不描述天驭内部实现。
>
> **本文档的三条纪律**：
> 1. **每个变更项有稳定编号（C1…C9）**，供 DR/IR/提交信息引用。
> 2. **每个变更项写明「现状 file:line → 目标 → 验收 → 不做什么」**，agent 可独立执行与自检。
> 3. **术语单列一节（第 2 节）**。本次设计讨论中反复出现术语混淆（"方言""producer""领域"），
>    落地时必须以第 2 节为准。

---

## 0. 一句话结论

> **领域不是页面，领域是插件；页面是"人要做的事"的容器，插件是填进容器里的料。**
>
> 本仓当前把本体词汇表复制在 4 处、把"应用"和"导出格式"混为一谈、把参数校验当成指标校验的变体。
> 本提议把这三处收敛为：**一个词表事实源（C1）**、**一个契约生命周期抽象（C2）**、
> **一套"导出格式"与"本体应用"的分离（C3/C4）**，并为未来 N 个领域接入留出零内核改动的通道。

---

## 1. 目标与非目标

### 1.1 目标

| # | 目标 | 验收口径 |
|---|---|---|
| G1 | 新增一种对象种类（设备 / 原子技能 / 复合技能）的成本降到「改一个 JSON」 | 加 kind 不动任何 `.py` |
| G2 | 参数与指标共用一套契约生命周期（状态 / 人审 / 失效 / 影响面） | 状态机代码**只存在一份** |
| G3 | 本体的标准导出与「用本体做出来的应用」彻底分离 | 导出格式集合与领域无关；应用由领域包注册 |
| G4 | 领域接入不新增顶级导航项、不新增页面 | 侧栏固定项 **+0**；领域应用动态追加 |
| G5 | 接入第二个领域时内核代码改动为 0 行 | 见 §8 验收标准 |
| G6 | 现有 565 个集成检查点不回归 | `test_all.py` 零失败 |

### 1.2 非目标（明确不做）

| 不做 | 理由 |
|---|---|
| ❌ 不做通用低代码平台 / 插件市场 | 判据：无法用「声明 → 编译 → 执行 → 比对 → 裁决」表达的，不进契约体系 |
| ❌ 不做远程加载领域包 | 领域包是本地可信代码，与 `mcp_build_server` 的「Agent 不能改写状态」红线一致 |
| ❌ 不让领域包携带前端 JS | 本仓明确「原生 JS，无前端构建步骤」；领域包只带 JSON 描述，渲染器在主程序 |
| ❌ 不重写 `ui/index.html`（2471 行） | 迁移期新老共存，新机制在旁边长出 |
| ❌ 不改 SQL 方言 / 不引入新依赖 | 本次不涉及执行层 |
| ❌ 不让 LLM 直接产出可执行产物 | 沿用全仓纪律：LLM 只提议，裁决与校验是确定性的 |

---

## 2. 术语（落地时以此为准，勿再混用）

本次设计讨论中出现过三处会导致实现走偏的术语混淆，先钉死。

### 2.1 "领域 / 节点 / 词表" 的关系

| 词 | 含义 | 数量关系 |
|---|---|---|
| **领域 domain** | 一个可插拔的贡献包：词表条目 + 证据来源 + 注册的应用 | **一个领域贡献若干 kind，不是一对一** |
| **节点 node** | 本体 IR 里的一个对象实例 | **全局唯一，不属于任何领域** |
| **词表 vocab** | 全局唯一的「kind → 名称/颜色/上层类别」映射表 | **一张表，领域往里追加，内核条目不可被覆盖** |

- IR 是全局单份；领域**不拥有节点**，领域只"消费"节点。
- 同一节点可被多个领域以不同方式解读（例：`ac07` 在机器人域是物料、在数据域是物料主数据）→ **正常，无需处理**。
- 同一节点被多个 producer/应用各自投影 → **各自产各的，输出通道互不共享**。

### 2.2 "导出格式" 与 "本体应用" 是两件事 ⚠️

|  | **导出格式 exporter** | **本体应用 application** |
|---|---|---|
| 是什么 | 本体 → 另一种**表示** | 本体 → 一个**领域行为** |
| 例子 | OWL / RDF / SHACL / JSON-LD / Ossie / 关系型投影 | 深度问数 / 根因诊断 / **行为树编排** / 图像语义问答 |
| 与领域关系 | **无关**，所有领域的本体都能导 | 分通用（内核）与领域（领域包注册） |
| 可逆性 | 有意义（导出可再导入） | 无意义（SQL 导不回本体） |
| 有无输入表单 | 无 | **有**（自然语言 / 任务描述 + 选本体） |
| 有无过程 | 无 | **有**（锚定 → 提议 → 裁决 → 校验 → 产物） |

> ❌ **不要**把"行为树"做成导出格式。行为树是应用。
> ❌ **不要**把"行为树生成"放进「本体构建」。它是应用，与「深度问数」同族。

### 2.3 "契约方言" → 改为"契约 spec + 各自的证据通道"

> ❌ **不要**用"方言"暗示"只有 spec 和 compile 不同"。
> 指标契约与参数契约的**证据通道不同**（见 C2），这是结构性差异，不是措辞差异。

|  | 指标契约 | 参数契约 |
|---|---|---|
| spec | 聚合 / 过滤 / 时间 / 维度 | 取值 / 单位 / 坐标系 / 作用对象 |
| **证据通道** | **执行参照**：编译成 SQL → 只读执行 → 与历史结果比 | **标定参照**：与标定记录 / 实测报告比 |
| 能否自证 | ✅ 能 | ❌ **不能** |
| 能否用机器人执行结果验证 | 不需要 | **绝对不能**（循环依赖：参数要先编译出树，树跑完才有结果） |

### 2.4 其他已用词

- **能力（function）**：一台设备能做的原子动作。天驭例：`MoveJ` / `MoveL` / `CloseGripper`。
- **技能（skill）**：由多个能力编排出的业务过程。天驭例：`Perception` / `Grasping` / `Place`。
- **任务编排（task）**：一次具体的编排 = 若干技能调用 + 实体绑定 + 条件引用 + 参数引用。**编译的主语是它，不是某个技能。**
- **能力模板**：技能内部的固定能力序列。**由人定义并确认，不随任务变化**，LLM 不得自由组合。

---

## 3. 架构总览

```
┌──────────────────────── 内核（领域无关）─────────────────────────────┐
│ IR · 证据分层 · 接地 · 规则 · 契约生命周期(C2)                          │
│ 词表 vocab(C1)                                                        │
│ 标准导出格式(C3)：OWL/RDF/SHACL/JSON-LD/Ossie/关系型投影  ← 领域无关     │
│ 通用应用(C4)：深度问数 · 根因诊断 · 业务助手 · 问数评测                 │
│ 应用外壳(C4)：选本体 · 锚定条 · SSE 步骤 · 三态验收 · 人审              │
└────────────▲──────────────────────────────────────▲───────────────────┘
             │ 追加词表条目                          │ 注册应用
             │                                      │
   ┌─────────┴──────────┐              ┌────────────┴──────────────┐
   │ 领域包：机器人       │              │ 领域包：图像 / 化工 …       │
   │ 词表 device/function/│              │ 词表 …                     │
   │      skill           │              │ 应用 图像语义问答           │
   │ 应用 行为树编排(C4)  │              │ 证据来源 …                 │
   │ 证据来源 ROS/标定    │              │                           │
   └──────────────────────┘              └───────────────────────────┘
```

**分工一句话**：
- **内核**回答"什么是合法的本体、什么是合法的契约、怎么验收"；
- **领域包**回答"这个领域有什么概念、从哪取证、能做哪些应用"；
- **应用**回答"用这个本体，能在自然语言驱动下产出什么"。

---

## 4. 变更项清单

### C1 · 词表下沉（`vocab.py`）

**现状（4 处重复，已发生漂移）**

| 位置 | 内容 |
|---|---|
| `ui/index.html:734` | `KIND`（kind → 颜色 + 中文名），**缺 `ice`** |
| `ui/index.html:904` | `GKIND`（kind → 画布配色），有 `ice` |
| `ui/index.html:909` | `EST`（关系状态 → 线型） |
| `content_quality.py:23` | `VALID_KINDS = {...}` |
| `ontology_grounding.py:14` | `KIND_DEFAULTS = {...}`（kind → BFO/IOF 上层类） |

**漂移实证**：前端 `KIND` 少 `ice` → 图例把 113 个信息内容对象（`workdir/built_motrix.json` 142 对象中 113 个为 `ice`）显示成原始字符串 `ice`，而画布 `GKIND` 里 `ice` 有配色。

**目标**

新增 `vocab.py` 作为唯一事实源，字段与取值见 §5.1。要点：

- 每条 kind 含：`cn` / `color`（标签色）/ `canvas`（画布 fill+stroke+text）/ `upper`（BFO/IOF 上层类别名）/ `editable`（评审台可否改类型）/ `source`（`core` 或领域名）。
- 关系状态 `relation_states` 收编 `EST`（含 `path`，该状态是锚定子图特有，与 `inferred` 同族）。
- `upper_classes` 只收**名字**；**IRI 映射仍归 `ontology_grounding`**（避免循环依赖）。
- 提供 `base_kind(k)`：剥掉领域限定名（`robot.slot` → `slot`），用于「两领域确实要用同词不同义」的少数场景。
- 提供 `merge(extra)`：领域包追加条目；**内核条目不可被覆盖**（抛错）——否则装一个领域就会改变其他领域本体的渲染与接地判定。
- 提供 `validate()`：上层类别名必须在 `UPPER_CLASS_IRIS` 内、颜色必须合法。

**改动清单**

| 文件 | 动作 |
|---|---|
| `vocab.py` | **新建** |
| `content_quality.py:23` | `VALID_KINDS` 改为从 `vocab` 读（**函数/常量名不变**，调用方无感） |
| `ontology_grounding.py:14` | `KIND_DEFAULTS` 改为从 `vocab` 派生；`default_category()`（`:110`）签名与语义不变；**`default_category()` 必须支持 `base_kind()` 兜底**（限定名场景） |
| `bp_vocab.py` | **新建** blueprint，`GET /api/ontology/vocab` 返回合并后的词表 + 领域清单 |
| `server.py` | 注册 blueprint（照 `app.register_blueprint(_bp_engine)` 的样子，1 行） |
| `ui/index.html:734/904/909` | 三个 const 改为启动时 fetch 注入。**首屏必须内联一份默认值再覆盖**，避免 fetch 未完成时闪烁或报错 |
| `tests/unit/test_vocab.py` | **新建**：合并幂等、内核条目不可覆盖、上层类别名合法、`ice` 在前后端一致 |

**验收**
1. `grep -rn "VALID_KINDS\|KIND_DEFAULTS" --include=*.py` 只剩 `vocab.py` 一处定义。
2. 图例里 `ice` 显示为「信息内容」而非 `ice`（**修掉已存在的 bug**）。
3. `test_all.py` 565 断言零失败。

**不做**：不改 `UPPER_CLASS_IRIS`（`:23`）、不改 `RELATION_SPECS`（`:54`）、不改 `VERB_RELATIONS`（`:91`）—— 它们是**关系**词表，不是**kind** 词表。给机器人领域加 `hasInput`/`hasOutput`/`realizes` 是**既有**官方关系，**不需要新增**，只是此前没人用。

---

### C2 · 契约抽象（`contracts/`）

**现状**

`metric_contract.py`（`:41` 起）把「状态机」「spec 形状」「编译」「执行」「比对」写在一起，且 spec 是指标专属的。

**目标**

拆成 `contracts/` 三层：

```
contracts/
  common.py    生命周期（共用，一份实现）
  metric.py    指标契约 spec
  param.py     参数契约 spec
```

**共用（`common.py`）—— 状态机只有这一份**

- `STATUSES = ("candidate", "verified", "certified", "deprecated")`（沿用 `metric_contract.py:40`）
- `STATUS_RANK`（`metric_contract.py:41`，`deprecated:-1, candidate:0, verified:1, certified:2`）
- 状态流转规则（沿用 `:479` adjudicate 与 `:629` upsert_layers 的口径）：
  - `verified` **只**来自「可执行 ∧ 与参照一致」，**无任何 API 可直接置 verified**；
  - `certified` **只**能由人授予（须 `reviewer`），且前置条件是「是契约 ∧ 可编译」；
  - 人工状态不被机器核验覆盖（`adjudicate` 里 `if human: return c` 的语义）；
  - `deprecated` 只停用不删除；撤回 = 置回 `candidate` 并清人工痕迹；
  - 人工改口径时不换口径（`_same_caliber` 语义）。
- 审计与影响面：`audit()` / `counts()` 的**结构**（阻断项 vs 待审项）
- **失效检测**（新增）：契约参照或上下文变化时标记为「待复核」

**两种 spec（各自独立，不共享验证机制）**

| | 指标契约 | 参数契约 |
|---|---|---|
| spec 字段 | `measure{col,agg}` `filters[]` `time{col,grain}` `dimensions[]` `entity` `unit` | `value` `unit` `frame` `scope`(作用对象) `owner_skill` |
| **证据通道** | **执行参照**：编译 SQL → 只读执行 → 与历史参照（历史 SQL / 口径表 / 沉淀 SQL / 基准）比 | **标定参照**：与标定记录 / 实测报告比；**或历史样本反解**（从历史图像统计 / 日志反解候选） |
| 编译 | `compile_sql()`（`:278` 现有实现） | 编译成 BT 端口取值绑定 |
| 执行 | `execute()`（`:379`，只读 + 8s 超时） | 校验：值在允许域、单位换算一致、引用存在 |
| 比对 | `compare_results()`（`:424`，形状必须一致、1% 相对容差） | 与标定参照值比（容差按物理量单独配） |
| 前置检查 | `schema_missing()`（`:325`）—— **SQLite 兼容模式下 `SUM("不存在的列")` 也会"成功"算出 0**，必须先查目录 | 存在性/单位/域的前置检查，同理 |

**参数契约的硬规则**

1. **没有标定证据 ⇒ 只能是 `candidate` ⇒ 不得填进任何产物端口。**
2. **绝不使用机器人任务执行结果校验参数**（循环依赖）。运行结果只能用于"发现问题、触发复核"。
3. `verified` 的来源是**独立标定活动**，不是任务执行。

**改动清单**

| 文件 | 动作 |
|---|---|
| `contracts/common.py` | **新建**：状态机、审计结构、失效检测 |
| `contracts/metric.py` | **新建**：从 `metric_contract.py` 迁移 spec + compile + execute + compare（**对外函数名与签名保持不变**） |
| `contracts/param.py` | **新建**：参数 spec + 标定证据通道 + 编译成端口绑定 |
| `metric_contract.py` | **降级为薄门面**：保留现有公开函数（`normalize` / `compile_sql` / `adjudicate` / `audit` / `describe` / `allowed_dimensions` / `is_contract` / `counts` / `upsert_layers` / `execute` / `schema_missing`），内部改为委托 `contracts/`。**外部调用方零改动** |
| `bp_metrics.py` | 新增 `type=metric\|param` 分支，或新增平行端点组 `/api/param/*`（`metric_contract_view` `:36` / `metric_dimensions` `:54` / `metric_status` `:67` / `metric_adjudicate` `:104` 的结构照抄） |
| `tests/unit/test_contracts_common.py` | **新建**：状态机不变量（人机不可越权） |
| `tests/unit/test_param_contract.py` | **新建**：无标定证据不得升级；拒绝用执行结果当参照 |

**验收**
1. `metric_contract.py` 中不再出现 `STATUSES` 字面量（只有 `contracts/common.py` 有）。
2. `bp_metrics.py` 的 `metric_status`（`:67`）对 `status` 的白名单仍是 `certified/deprecated/candidate`，**`verified` 依旧不可直接置**。
3. 现有指标相关断言（`test_all.py` 指标段）零失败。

**不做**：不改 `metric_contract.py` 的**行为**，只改**代码位置**；不改状态机取值；不引入"参数 verified 可由任务执行结果产生"的口子。

---

### C3 · 标准导出格式注册表（`exporters/`）

**现状（散在三处）**

| 位置 | 格式 |
|---|---|
| `server.py:2779` `graph_export_fmt` | ttl / jsonld / owl(rdfxml) |
| `bp_semantic.py:34` | Ossie（semantic_model / ontology） |
| `ir_relational.py`（`:120` `project` / `:181` / `:193`） | 关系型投影 —— **只有 CLI/模块，无 HTTP 端点** |

**目标**

统一为内核的 `exporters/` 注册表，**与领域无关**：

```python
EXPORTERS = {
  "ttl":        {"label": "Turtle",        "mime": "text/turtle",           "fn": ...},
  "owl":        {"label": "RDF/XML",       "mime": "application/rdf+xml",   "fn": ...},
  "jsonld":     {"label": "JSON-LD",       "mime": "application/ld+json",   "fn": ...},
  "osi":        {"label": "Ossie",         "mime": "text/yaml",             "fn": ...},
  "relational": {"label": "关系型投影",     "mime": "application/x-sqlite",  "fn": ...},
}
```

- `GET /api/export/list` 返回清单 → **前端导出菜单动态生成**，加格式不用改前端。
- `ir_relational` 补一个只读 HTTP 端点（它已经是**纯函数、确定性、状态不提升、证据随行**的合格消费出口，只差一个路由）。
- **顺手补**：`/api/sparql`（`server.py:3405`）目前只有 POST JSON，缺标准 W3C 形式（`GET /sparql?query=` + `Accept` 协商），外部标准工具对接会不适配。

**改动清单**：`exporters.py`（新建）· `server.py:2779` 改为查表 · `bp_semantic.py:34` 并入 · `ir_relational` 补端点 · `server.py:3405` 补 GET 形式。

**验收**：导出菜单项来自 `/api/export/list`；加一个新格式只需注册一个条目。

**不做**：**不把行为树注册成导出格式**（见 §2.2）。不改变任何格式的现有输出内容。

---

### C4 · 应用框架（`apps/`）

**目标**

把「以本体为输入、以自然语言为入口、产出领域产物」的一类操作抽象成**应用**，共享一条管道：

```
选本体 → 自然语言输入 → 锚定/召回 → 提议 → 确定性裁决 → 校验 → 产物 → 带出处
```

现有可复用件（**不要重写**）：

| 环节 | 现有实现 |
|---|---|
| 选本体 + 锚定 | `server.py:952` `_anchor_ir` · `server.py:1071` `_qa_anchor_ir` |
| 上下文组装 | `server.py:1124` `build_context` |
| 提议（LLM） | `server.py:1534` `agent_sql_plan` 的提示词/输出契约范式 |
| 确定性校验 | `server.py:1363` `_validate_sql_ontology`（表白名单 / JOIN 键 / 扇出）· `server.py:1429` `_qa_validate_sql` |
| 步骤推送 | `ui/modules/build-stream.js` + 构建链路的 SSE |
| 三态验收 | `build_quality.py` 的 pass/review/fail |
| 人审 | `/api/ont/apply` 白名单编辑 + 评审台 |
| 拒答 | `server.py:3869` `diagnose_stream` 的"实体未命中即拒答" |

**新增**

```
apps/
  framework.py     应用外壳：选本体 · 锚定条 · SSE 步骤 · 三态验收 · 人审
  registry.py      应用注册表
  deepqa.py        深度问数（现有能力，改造为注册进框架）
  diagnose.py      根因诊断（从深度问数的 mode 提为一级应用）
  btcompile.py     行为树编排（机器人领域包注册的第一个验证者）
```

每个应用声明：

```python
APP = {
  "id": "btcompile",
  "name": "行为树编排",
  "icon": "tree",
  "group": "本体应用",
  "consumes_kinds": ["skill", "function", "device"],   # 需要哪类本体才可用
  "input_schema": {...},          # 输入表单（自然语言 / 任务描述 / 选项）
  "steps": [...],                 # 管道节点（复用框架的阶段）
  "product": {...},               # 产物渲染器
}
```

**关键约束**

- **通用应用**（深度问数、根因诊断）与**领域应用**（行为树编排、图像语义问答）共用同一外壳，只有输入表单与产物渲染器不同。
- 应用**不新增顶级导航组**；组内每应用一项，且**领域应用由领域包动态注册**（没装包不显示）。

**验收**：第一个新应用（行为树编排）**不新增一行外壳代码**即可跑通。

---

### C5 · 领域包（`domains/`）

**新增**

```
domains/
  base.py           DomainContext：kind / source / app / exporter 注册
  registry.py       启动扫描 domains/*/manifest.json
  robotics/
    manifest.json   声明贡献了什么
    vocab.py        kind: device / function / skill
    sources/        证据来源（ROS 接口契约 / 行为树 XML 逆向 / 标定记录）
    apps/btcompile.py
```

**领域包可贡献四类东西**

| 贡献点 | 说明 | 机器人域的例子 |
|---|---|---|
| **词表条目** | 追加 kind（不可覆盖内核条目） | device / function / skill |
| **证据来源** | 从哪取证、拿什么裁决 | ROS 接口契约、行为树 XML、标定记录 |
| **应用** | 注册领域应用 | 行为树编排 |
| ~~导出格式~~ | ❌ 不需要 | 导出格式是内核能力，与领域无关 |

**证据来源的插件点**（`source.collect` / `source.adjudicate`）

裁决协议（判定三态、证据分层、人工确认产生 `asserted`）**留在内核**，不随领域变。变的只是"证据"：

| | 数据库领域 | 机器人领域 |
|---|---|---|
| schema | 表 → 列 → 外键 | ROS 接口契约 + BT 节点模型端口 |
| 连接键验证 | 值域重叠 ≥60% ∧ 父键唯一 ∧ 命名有据 ∧ 方向正确 | **同一实体的跨系统 ID 对齐** ∧ 资源唯一性 ∧ 契约存在性 |
| 方向 | 表间基数方向 | 动作方向（谁生产谁 / 谁包含谁） |

> ⚠️ **红线**：**不得**把行为树 XML 当"表"喂 `dao_core` 去算值域重叠。那会产出**看起来可信、实则伪造的 `verified`**。
> 天驭的裁决证据必须是**接口契约核对**与**实测标定比对**。

**领域包是本地可信代码**：不远程加载、不带前端 JS、只带 JSON 描述 + 确定性 Python。

---

### C6 · 天驭领域的本体扩展（具体要建什么）

**词表条目**（追加进 `vocab`，不改内核条目）

| kind | cn | upper | 说明 |
|---|---|---|---|
| `device` | 设备 | `MaterialArtifact` | UR 机械臂 / 相机 / 夹爪 / 载具 / 工位 |
| `function` | 能力 | `Function` | `MoveL` / `MoveJ` / `CloseGripper` / `CaptureFrame` |
| `skill` | 技能 | `PlannedProcess` | `Grasping` / `Perception` / `Place` |

**关系**（**全部是既有官方关系，无需新增**）

| 关系 | IRI | 定义域 → 值域 | 天驭用法 |
|---|---|---|---|
| `bearerOf` | `obo:BFO_0000196` | MaterialArtifact → Function | UR 机械臂 **承载** MoveL |
| `realizes` | `obo:BFO_0000055` | Process/PlannedProcess → Role/Disposition/Function | 抓取技能 **实现** MoveL |
| `hasInput` | `iof:hasInput` | Process/PlannedProcess → Continuant | 抓取技能 **输入** ac07 |
| `hasOutput` | `iof:hasOutput` | Process/PlannedProcess → Continuant | 抓取技能 **输出** ac07_in_gripper |
| `hasParticipantAtSomeTime` | `obo:BFO_0000057` | Process/PlannedProcess → Continuant | 设备 **参与** 技能 |

**能力 ↔ 设备的双重表达**：`bearerOf`（设备拥有能力）+ `participatesInAtSomeTime`（能力参与技能）。

> ⚠️ **`hasInput`/`hasOutput` 的源只允许 Process/PlannedProcess**（`ontology_grounding.py:61-66`）。
> 所以 **能力不能有 hasInput/hasOutput** —— 能力的输入输出是**端口上的数据**，在 BT 里是 `{key}`，**不是本体对象**。
> 这与 BT 语义天然吻合。

**IR 扩展**：需新增 `skills[]`（技能模板：固定能力序列 + 端口骨架 + 前置条件 + 收尾 + 独占资源）与 `skill_invocations[]`（任务调用：绑定 + 参数引用 + 条件引用）。

**语法必须区分两种引用**（最易错处）：

| 语法 | 含义 | 何时解析 |
|---|---|---|
| `{key}` | 黑板键 | **运行期**（数据流） |
| `@param:<id>` | 本体参数对象引用 | **编译期**（取值来源，带状态） |

混用会导致树里出现"永远没人写的黑板键"，**只在运行期才炸**。

**`function` kind 必须显式加入 `KIND_DEFAULTS`**：`default_category()`（`ontology_grounding.py:110`）对未知 kind 兜底成 `Continuant`，而 `Continuant` **不在** `bearerOf` 的源集内 → 所有设备-能力关系会**静默变成 unmapped**。**不能靠兜底。**

---

### C7 · 行为树编译链（天驭侧应用）

**输入**：自然语言描述 + 选定的本体
**产物**：一次任务编排（任务 + 树 + 编译清单）

**管道**（与「构建本体」同形状，复用 C4 框架）

| 步 | 内容 |
|---|---|
| 范围与任务 | 归一目标、源/目标位置、操作对象、验收问题 |
| 锚定 | 命中对象 / 技能 / 能力 / 规则 / 参数 |
| 提议（LLM） | 只**引用**本体中已有物：技能调用、实体绑定、参数引用（`@param:`）、条件引用（`rule:`） |
| 裁决（确定性） | 引用是否存在于本体、参数状态、规则有效性、绑定是否在规则作用范围内 |
| 编译（确定性） | 任务 → BT XML |
| 质量闸门 | 见下表 |
| 人审 | 复用评审台 |

**LLM 的三条硬边界**（违反即编译失败，不是提醒）

1. **不得给裸参数值** —— 参数只能 `@param:` 引用参数对象。
2. **不得自造条件** —— 只能 `rule:` 引用已有规则。
3. **不得自造能力组合** —— 技能的能力组成是**固定模板**，由人定义并确认。

**质量闸门（与构建本体共用三态：pass / review / fail）**

| 检查 | 级别 |
|---|---|
| 引用完整性（技能 / 条件 / 参数 / 实体都在本体里） | **fail** |
| **参数状态闸门**：只有 `certified` 可无条件填端口；`verified` → review；`candidate`/`deprecated` → fail | **fail** |
| 规则有效性：规则存在 + 作用对象在范围内 + **黑板键有写入者** | **fail** |
| 规则冲突（同一条件不同结论） | **fail** |
| 独占资源冲突 / 重复占用 | **fail** |
| **单位 / 坐标系漂移**（树 / 能力 / 参数三者口径一致） | **fail** |
| 能力组合漂移（模板 `steps[]` 签名未变） | **fail** |
| 收尾动作存在且无条件执行 | **fail** |
| 存在但无任何产物引用（孤立） | review |
| 引用了但状态为 `verified`（未经业务确认） | review |

> ⚠️ **单位/坐标系漂移必须 fail**：这是唯一会**静默出错**的类别（位姿数值都在合理范围、机器人不报错、就是抓偏）。
> 编译时把每个带单位的取值记进**编译清单**，校验时逐条比对。

**技能内部的固定模板 + 收尾必须独立**

```
Sequence
├─ Precondition  slot_available == 'FREE'      ← 条件在 Precondition 内
│  └─ SubTree grasp_from_pose
└─ Sequence                                      ← 收尾在 Precondition 外层
   └─ SubTree retreat_to_pre_grasp
```

收尾在 `Precondition` **外面** ⇒ 感知失败导致内层 FAILURE 时**撤离照样执行**。
纯 Sequence 做不到这一点（天驭现有 `xingweishu.xml` 的 `grasp` 正是这个缺陷：`CloseGripper` 失败 → 末尾 `MoveL` 撤离被跳过 → 机器人停在抓取位）。

**编译清单 manifest**：记录编译时的本体指纹（`ir_digest`，思路同 `osi_export` 模型级扩展）、每个带单位取值的值 + 单位 + 坐标系、每个引用的规则与参数的状态。**本体变了 → 指纹对不上 → 产物标为待复核，不被误当最新的下发。**

---

### C8 · 证据来源扩展（天驭侧）

新增两类可插拔来源，与数据库来源走同一裁决协议：

| 来源 | 采集什么 | 裁决依据 |
|---|---|---|
| **ROS 接口契约** | `ros2 topic/service/action list -t` + `ros2 interface show` → 端点 / 消息类型 / 字段 | **同一实体的跨系统 ID 对齐** ∧ 资源唯一性 ∧ 契约存在性 |
| **行为树 XML** | 技能 / 步骤 / 数据流 / 参数字面量 | 结构事实 → `asserted`；**手写字面量 → `candidate`** |

**逆向构建的抽取规则**（从现有 BT XML 反解本体）

| XML 模式 | → 本体 |
|---|---|
| `<BehaviorTree ID="X">` | 复合技能 X（`kind=skill`） |
| `<Sequence>` 子节点顺序 | 技能模板的 `steps[]`（**hasStep / stepIndex 的现成数据**） |
| `<SubTree ID="X" port="{key}">` | 技能 X 的输入端口；与父技能生成 `hasInput` / `hasOutput` 关系 |
| 属性值带 `{key}` | 该步骤读写的黑板键（数据流边） |
| 属性值是**字面量** | **参数候选值**，来源="手写字面量"，**无依据 → `candidate`** |
| `service_name` / `action_name` / `topic_name` | 设备绑定（`/ur/MoveJ` → UR 机械臂） |

**接口契约要采两套**（**单采 ROS 一套会漏**）

| 层 | 内容 |
|---|---|
| ROS 侧 | `.msg` / `.srv` / `.action` / IDL 定义 + 端点名 + QoS |
| 天驭内部 | C++ 端口声明（`robot_types::PoseQuat` 的字段序 / 单位 / 坐标系） |

> ⚠️ **已知隐患**：`robot_types::PoseQuat` 在 `xml_models.xml` 里两处注释的**字段序不一致**（`GetTCP.pose` 写 `[x,y,z,w,x,y,z]`，`FrameTransform.robot_pose` 写 `[x,y,z,qw,qx,qy,qz]`），而 ROS `geometry_msgs/Quaternion` 是 `x,y,z,w`。
> **转换必须重排**；重排错了数值合法但不正确，机器人不报错只是抓偏。
> → 接口契约采集必须产出**显式的转换规则**（`from` / `to` / `rule` / `verified`），并纳入 C7 的单位/坐标系漂移检查。

---

### C9 · UI 改造

**原则**：**领域不是页面，领域是插件；页面是"人要做的事"的容器，插件是填进容器里的料。**

| 动作 | 内容 | 侧栏净增 |
|---|---|---|
| 改名 | 侧栏组「智能问数」→ **「本体应用」** | 0 |
| 保留 | 深度问数 · 业务助手 · 问数评测（位置不变） | 0 |
| 新增（动态） | **根因诊断**（从深度问数的 mode 提为一级）· **行为树编排**（装机器人包才出现） | 0（动态） |
| 改造 | 深度问数 / 行为树编排 **共用应用外壳**：选本体 · 锚定条 · SSE 步骤 · 三态验收 | — |
| 改造 | 契约中心（现「指标中心」）：加 Tab「参数契约」 | 0 |
| 改造 | 数据连接：数据源类型加「ROS 接口契约」「行为树 XML」 | 0 |
| 改造 | 深度问数：锚定源加「执行记录库」 | 0 |
| 改造 | 本体图谱：选中本体 → 显示「可用应用」列表（能力发现） | 0 |
| 改造 | 技能中心：加 Tab「业务技能」「能力原语」 | 0 |
| **撤销** | ❌ 上一版把「编排行为树」放进「本体构建」Tab | — |

**应用外壳**

```
┌ 本体应用 · <当前应用> ──────────────────────────────┐
│ [深度问数] [根因诊断] [行为树编排] [图像语义问答]      │  ← 应用切换（数据驱动）
│ ────────────────────────────────────────────────────│
│ 本体：[<选中的本体> ▾]        ← 所有应用都必须先选本体 │
│ ────────────────────────────────────────────────────│
│ 输入区（应用特有）                                    │
│   深度问数   ：[问句]                                 │
│   行为树编排 ：[任务描述] + [验收问题]                 │
│ ────────────────────────────────────────────────────│
│ 锚定条（共用）本体 51 对象 → 命中 6 → 扩展 4 → …      │
│ ────────────────────────────────────────────────────│
│ 步骤推送（共用 SSE）                                  │
│ ────────────────────────────────────────────────────│
│ 产物区（应用特有）                                    │
│   深度问数   ：SQL + 表 + 口径卡 + 出处                │
│   行为树编排 ：BT XML + 编译报告 + 参数溯源 + diff     │
└──────────────────────────────────────────────────────┘
```

**"未来 N 个需求进来怎么不臃肿"的操作性定义**：

1. 每个应用 = **一个注册条目** + **一个输入表单** + **一个产物渲染器**；外壳一行不改。
2. 侧栏增长 = **线性**（每应用 1 项），且**有条件出现**（没装领域包不显示）。
3. 通用应用（内核提供）与领域应用（领域包注册）在界面上**没有区别**。

**前端硬约束**：本仓明确「原生 JS，无前端构建步骤」。
→ **领域包不带 JS**，只带 JSON 描述；渲染器在主程序，数量少、可控。违反这条 = 引入任意代码注入面。

**参考原型**（本仓内，可直接打开）

| 文件 | 内容 |
|---|---|
| `docs/ui-prototype/robot-domain.html` | 7 幕全景：侧栏 / 本轮改动 / 本体图谱 / 编排行为树 / 产物库 / 契约中心 / 数据源与问数 |
| `docs/ui-prototype/panel-drawer-demo.html` | 面板 vs 抽屉专项演示（单机离线） |

---

## 5. 关键数据结构

### 5.1 `vocab.py`

见 §4 C1。要点：字段清单、限定名、合并规则、校验规则。

### 5.2 契约通用信封

```json
{
  "id": "ac07.detect_threshold",
  "type": "parameter",
  "name": "detect_threshold",
  "cn": "检测阈值",
  "status": "candidate",
  "spec": {"value": 0.8, "unit": "1", "frame": null,
           "scope": "ac07", "owner_skill": "perception_at_station"},
  "evidence": {"kind": "calibration", "reference": "CAL-2026-014",
               "executed": true, "match": null, "checked_at": null},
  "certified_by": null, "certified_at": null,
  "provenance": [{"source": "bt_reverse", "note": "手写字面量"}]
}
```

`evidence.kind` 取值：`execution`（指标，执行参照）/ `calibration`（参数，标定参照）/ `history`（反解候选）。
**`evidence.kind` 决定了 `verified` 怎么产生**，不允许跨通道。

### 5.3 任务编排

```json
{
  "task": {
    "id": "move_ac07_to_B01",
    "cn": "把 ac07 从工位A搬到载具B01的槽位3",
    "graph": "robot_cell",
    "invocations": [
      {"seq": 1, "skill": "perception_at_station",
       "bindings": {"part": "ac07", "station": "station_A"},
       "params": {"model_name": "@param:ac07.detect_model"},
       "preconditions": ["rule:part_visible"]}
    ],
    "preconditions": ["rule:part_in_position", "rule:slot_available"],
    "exclusive_resources": ["slot_B01_3"],
    "tree": "<BT XML>",
    "manifest": {"ontology_digest": "...", "compiled_at": "...", "bindings": [...]}
  }
}
```

---

## 6. 实施顺序（**不要先做插件框架**）

| 阶段 | 内容 | 人日 | 性质 | 风险 |
|---|---|---|---|---|
| **P0** | C1 词表下沉 + 修 `ice` 漂移 | 1 | 纯重构 | 极低 |
| **P1** | C2 契约抽象（`contracts/`） | 4–6 | 抽象 + 1 个新 spec | 中 |
| **P2** | C3 导出格式注册表 + `ir_relational` 补端点 + SPARQL GET | 2 | 纯重构 | 低 |
| **P3** | C4 应用框架（`apps/framework.py` + 深度问数改造为注册应用） | 5–8 | 新机制 | 中 |
| **P4** | C5 领域包机制 + C6/C7/C8 机器人域作为**第一个真实使用者** | 8–12 | 新机制 + 业务 | 中 |
| **P5** | C9 UI 改造 | 3–5 | 新机制 | 中 |

> ⚠️ **最容易做死的路径：先做通用插件框架，再把现有内容迁进去。**
> P0–P2 全是**对现有代码的重构，不引入新概念**，每一步都能独立交付价值。
> P3–P5 才引入新机制 —— 那时已有 2 个 spec、6 个导出格式验证过抽象是否成立。

**P0–P2 不含**：ROS 契约采集器、行为树逆向构建器、BT 编译器、`bt_quality` 校验器、参数契约的标定比对实现。**那些是 C4/C7/C8 的工作量，与架构无关。**

---

## 7. 回归红线

| # | 红线 | 检查方式 |
|---|---|---|
| R1 | `test_all.py` 565 断言零失败 | 集成层套件 |
| R2 | 单元层 coverage 不低于 81.0% | `pyproject.toml` 既有阈值 |
| R3 | **只读三重防写不被削弱**（`ro_connect` mode=ro + `sql_is_readonly` + 单句执行） | `srv_context.py` 不动 |
| R4 | **MCP 的"发起动作是唯一写工具"不被削弱**（`mcp_action_server.py` 工具集恰为 6 个、审批不暴露） | `tests/unit/test_mcp_action_server.py:108-115`（`test_approval_is_never_exposed_as_a_tool` 断言 `writers == {"invoke_action"}`） |
| R5 | **`verified` 仍不可由任何 API 直接置**；`certified` 仍只能人授 | C2 状态机不变量测试 |
| R6 | **不得出现跨证据通道的 `verified`**（参数用执行结果、指标用标定记录） | 新增测试 |
| R7 | 领域包**不得覆盖内核 kind 条目** | `vocab.merge()` 抛错 + 测试 |
| R8 | 领域包**不带前端 JS**、不远程加载 | 代码评审 + 目录约定 |
| R9 | **不得把 BT XML 当表喂 `dao_core`** 算值域重叠 | 代码评审 + 无 `sqlite_evidence` 调用 |
| R10 | 现有 115 条 `server.py` 路由**除新增外签名不变** | 路由快照测试 |

---

## 8. 验收标准

**G5 的操作性判据**（最重要）：

> 接入**第二个领域**（化工 / PCBA —— `build_references.py:22` `INDUSTRIES` 已有 `chemical` / `pcba` 条目）时：
>
> - 内核代码改动 = **0 行**
> - 顶级导航项 = **0 个**
> - 新页面 = **0 个**
>
> **做不到，就说明抽象没做对。**

化工域应贡献：词表（reaction / substance / batch）、证据来源（DCS 点表 + 工艺规程 PDF）、契约 spec（收率 / 纯度）、产物（批次工艺卡）。

**分阶段验收**

| 阶段 | 验收 |
|---|---|
| P0 | 图例里 `ice` 显示「信息内容」；加 kind 不动 `.py` |
| P1 | 状态机代码只存在一份；参数无标定证据不得升级；指标相关断言零失败 |
| P2 | 导出菜单来自 `/api/export/list`；关系型投影有 HTTP 端点 |
| P3 | 深度问数作为注册应用跑通，外壳可被第二个应用复用 |
| P4 | 从 `xingweishu.xml` 逆向出技能模板 + 能力目录；编译出带条件与收尾的 BT |
| P5 | 侧栏固定项 +0；领域应用动态出现；第二个领域接入零内核改动 |

---

## 9. 给 Agent 的执行须知

1. **先读 §2 术语**，再读 §4 变更项。术语混淆是本次设计最大的返工来源。
2. **一次只做一个变更项**，做完跑对应验收，不要连带改其他项。
3. **改动既有文件时保持对外签名不变**（`metric_contract.py` 的公开函数、`ontology_grounding.default_category()`、`content_quality.VALID_KINDS` 的名字）。调用方零改动是硬要求。
4. **新增 `.py` 必须有对应 `tests/unit/` 单测**；新端点必须进 `test_all.py`。
5. **不确定时，先做 P0/P1/P2 的纯重构部分**，它们风险最低、收益立即可见。
6. **不要**在本提议未落项前修改 `specs/decisions/` 下的既有 DR。落项时新起 DR 编号并在本文件标注对应关系。
7. **完成后**在本文件 §10 记录实际结果（哪些做了、哪些没做、实测数字），供下一轮 agent 判断。

---

## 10. 落项记录（待填）

| 变更项 | 状态 | 对应 DR/IR | 提交 | 备注 |
|---|---|---|---|---|
| C1 词表下沉 | 未开始 | — | — | |
| C2 契约抽象 | 未开始 | — | — | |
| C3 导出格式注册表 | 未开始 | — | — | |
| C4 应用框架 | 未开始 | — | — | |
| C5 领域包机制 | 未开始 | — | — | |
| C6 天驭本体扩展 | 未开始 | — | — | |
| C7 行为树编译链 | 未开始 | — | — | |
| C8 证据来源扩展 | 未开始 | — | — | |
| C9 UI 改造 | 未开始 | — | — | |

---

## 11. 关联材料

| 材料 | 位置 |
|---|---|
| 界面形态原型（7 幕） | `docs/ui-prototype/robot-domain.html` |
| 面板/抽屉演示（单机离线） | `docs/ui-prototype/panel-drawer-demo.html` |
| 构建链路（现有，22 节点） | `docs/pipelines/ontology_build.yaml` |
| 规约索引 | `specs/map.md` |
| 上层类别与关系接地 | `ontology_grounding.py` |
| 指标契约（现有实现，C2 的迁移源） | `metric_contract.py` |
| 规则与决策层 | `rule_engine.py` |
| 动作层投影 | `action_ontology.py` |
| 标准资产（固定版本 + 指纹） | `ontology/standards/` · `standard_assets.py` |
| 导出（Ossie / 关系型） | `osi_export.py` · `ir_relational.py` |
