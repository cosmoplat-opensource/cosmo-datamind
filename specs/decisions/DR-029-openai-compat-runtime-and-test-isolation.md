# DR-029 · OpenAI 兼容运行时与回归隔离

- **状态 / Status**: accepted
- **日期 / Date**: 2026-08-02
- **关联 / Refs**: [[DR-003-runtime-neutral-naming]](多引擎抽象)、[[DR-017-engine-settings]]、[[DR-027-business-aliases-and-change-audit]]

## 背景 / Context

**其一,运行时绑定问题**。深度问数与本体构建都经 `agent_runtime` 调 LLM,而该抽象由
上游引擎提供,内置驱动 claude-code / hermes / openclaw **都依赖本机装好特定 CLI**。
开源用户拿到仓库后,若手上只有一个 OpenAI 兼容 API(GLM、DeepSeek、Qwen、Moonshot、
vLLM 自建……),就无从接入——这与"开箱可用"相悖,也把系统绑死在少数几家。

**其二,回归污染运行态数据**。上一轮实测时发现:给对象加了业务别名,`/api/ont/cq`
立刻认,但跑完一轮回归后别名就没了。这不是偶发——回归会在 `demo` 图谱上做写操作,
把运行态本体改脏。

## 决定 / Decision

1. **`openai_runtime.py`**:在 DataMind 侧实现通用 OpenAI 兼容驱动,
   任何 `/chat/completions` 端点都能接,配置全走环境变量
   (`DATAMIND_LLM_BASE` / `_KEY` / `_MODEL`,`CLAW_DRIVER=openai` 选用),
   不改任何上游代码。

2. **回归沙箱**:回归自建 `built_regress` 独立图谱(复制 demo IR;`built_` 前缀由
   `load_ir` 直接从 workdir 加载,无需改服务端),所有**新增写操作**打在沙箱上,
   收尾清理。

## 为什么这样做 / Rationale

### 运行时

- **不注册就不出现**。未配置端点时 `available()` 返回 False 且不注册,系统按既有
  逻辑降级到模板兜底——绝不能因为"多了一个驱动"就让上游误以为 LLM 可用。
- **推理模型的空 content 必须当失败**。GLM-5 一类会把 token 预算耗在
  `reasoning_content` 上而 `content=""`。若当成功回传,上游会拿空串去解析 SQL 计划,
  变成极难排查的静默故障。实测中 glm-5 正是如此,故默认示例用 glm-4.5。
- **失败信息剔除鉴权头**,避免 Key 随日志外泄。

### 回归隔离

- **真凶不是 undo,是 `rebuild`**。排查时先怀疑 undo 撤过头,加了栈深守卫仍未解决;
  逐段二分才定位到 `E. 编辑/写` 分区里的 `POST /api/ont/rebuild` ——
  它**清空整个草案层**,一次调用就把 demo 的全部编辑(含业务别名)抹掉。
  这个教训值得记下:"看起来最可疑的"未必是真因,二分定位比猜测可靠。
- **U 分区(跨读方一致性)必须留在 demo**。它验证的是"写→图谱页/总览/问数提示/
  SPARQL/评审页即时可见",而 `build_context`、`/api/overview` 在服务端硬绑 demo,
  切沙箱就测不到这个了。该区块自带撤销复原,不是污染源。
  我一度把它整体切到沙箱,发现读方对不上后回退——**隔离要按"是否可复原"划线,
  不是按"是否写入"一刀切**。
- **盲撤固定次数改为栈深守卫**。`for _ in range(3): undo` 会在栈本就不深时
  连带弹掉他人条目。改为撤到基线深度即停。

## 影响 / Consequences

- 系统可接任意 OpenAI 兼容 LLM;`/api/ont/runtimes` 现返回
  `["claude-code", "hermes", "openai"]`。
- 回归跑完 `demo` 编辑栈深不变、业务别名存活、沙箱零残留。

## 代价与边界 / Trade-offs

- 本驱动**无状态**:每轮独立请求,不维护服务端会话。多轮指代靠上游把历史拼进
  prompt(既有 `_carryover` 机制已覆盖),不依赖 provider 的 session。
- 沙箱是 `demo` 的**副本**:若 demo IR 本身缺某类数据(如无候选关系),沙箱同样缺,
  相关断言仍会跳过。这是数据依赖,不是隔离机制的缺陷。

## 实测佐证(GLM coding plan)

- 深度问数:两场景经 GLM 生成 SQL 计划(33.5s / 43.6s),双盲均判 `意图一致`,
  别名锚定生效("产量趋势"正确命中生产日汇总,出 17 行)。
- 半自动构建:九步全绿,意图解析策略由 GLM 真实生成("基于示例主库提取核心实体,
  建立销售订单驱动生产工单并流转库存物料的……"),产出 16 对象 / 13 关系,
  反造假裁决 verified 12 / candidate 1;产物经 CQ、漂移(100%)、完备度(80.0)复查。
