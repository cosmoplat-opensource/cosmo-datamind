# DR-003 · 多引擎运行时抽象 + 对外中性命名 + 限流兜底

- **状态 / Status**: accepted
- **日期 / Date**: 2026-07-07
- **决策者 / Deciders**: Boss(用户) + Writer(Claude)
- **关联 / Refs**: `server.py` `agent_sql_plan`/`narrative_llm`/`_llm_extract_ontology`/`_eng_label`/`engName`(前端);平台 DR-002(LLM 经 agent_runtime)

## 上下文 / Context
LLM 能力经平台 `agent_runtime` 提供,底层驱动可为 hermes(GPT-5.5)/claude-code/openclaw,经 `CLAW_DRIVER` 选择。
两点诉求:①单个引擎会限流(HTTP 429 usage limit)或超时,不能让一次请求整体失败;②对外不暴露底层多智能体库名。

## 决定 / Decision
- **多引擎顺序兜底**:计划(`agent_sql_plan`)、洞察(`narrative_llm`)、本体抽取(`_llm_extract_ontology`)均按 `("hermes","claude-code")` 顺序尝试;`_looks_like_error()` 识别 `429/usage limit/rate limit/HTTP 4xx/5xx` 等把「ok=True 但正文是错误」的响应判为失败,跳到下一引擎。任一成功即返回。
- **对外中性命名**:后端 `_eng_label(drv)` 与前端 `engName(drv)` 把 `hermes→智能引擎`、`claude-code→智能引擎(备选)`、`openclaw→经典引擎`,用于**所有用户可见处**(顶栏引擎徽章、构建/深度问数执行记录的步骤名与状态文案、系统管理/运行时切换下拉、README)。底层驱动 id 仅存于代码与映射注释。
- 运行时切换 UI 在「构成规则」页,下拉 value 仍是真实 id(功能不变),仅显示中性名。

## 后果 / Consequences
- (+) 单引擎限流/超时不致整体失败:实测主引擎 429 时自动落备选,结果照常产出(仅多花 ~17s)。
- (+) 对外一致中性,不泄漏 hermes/claude-code/openclaw。
- (−) 主引擎限流时每次白耗 ~17s(其内部 3 次重试);无法预判限流,只能事后识别兜底。
- (−) 兜底顺序静态;两引擎都限流时 LLM 步骤失败,交由上层兜底(深度问数走模板、构建走 quick_build)。
