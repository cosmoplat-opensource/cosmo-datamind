# DR-004 · 深度问数:SSE 流式执行 + 数据源限定 + 缓存 + 兜底

- **状态 / Status**: accepted
- **日期 / Date**: 2026-07-06
- **决策者 / Deciders**: Boss(用户) + Writer(Claude)
- **关联 / Refs**: `server.py` `chat_stream`/`_qa_key`/`build_context`/`fallback_plan`;`ui/index.html` `ask()`;对齐平台 chat-bi·deep

## 上下文 / Context
要复现并超过平台 chat-bi「深度问数」:实时执行记录、渐进出图、数据源&技能选择、上传可查、沉淀技能。
需在引擎慢/限流/离线时仍可用,且不返回陈旧/串会话结果。

## 决定 / Decision
- **SSE 流式**(`/api/chat/stream`):逐条推送执行步骤(scope_source→load_ontology→match_schema→build_context→llm_plan→gen_sql→exec_sql→data_check→gen_chart→review→narrative),末尾 `done` 带完整结果;`result` 事件渐进出图(每完成一图即出)。
- **引擎计划后台线程 + 主循环轮询 `plan_steps`**(GIL 安全)实时外推 + 心跳;`agent_sql_plan` 生成 SQL 计划,离线/超时走 `fallback_plan` 内置模板。
- **数据源限定**:前端传 `tables`(直接作 focus)与 `graphs`(后端解析该图谱绑定表并入 focus_tables——否则「按图谱选源」是静默空操作)。
- **缓存 `_QA_CACHE`**:键 `_qa_key(question, history, focus)` **并入 uploads.db mtime**(上传变更即作废,防同名表复用陈旧结果);仅缓存成功结果,上限 200。
- **健壮性**:`sse()` 用 `json.dumps(..., default=str)` 防 BLOB 等不可序列化值让整条流静默中断(前端卡「运行中」);前端中止后 `finally` 复位发送按钮。
- 洞察经 `narrative_llm`(多引擎兜底,见 [[DR-003]]);失败走 `_rule_summary` 规则化摘要(基于真实数据不编造)。

## 后果 / Consequences
- (+) 与平台 chat-bi 观感一致且更稳:22 步执行记录 + 渐进出图 + 数据源/技能弹窗 + 沉淀为 Skill + 历史对话。
- (+) 引擎慢/离线可用(模板兜底);缓存命中 0s 秒回且不串数据源/上传。
- (−) 单次深度问数在引擎慢时可耗时较长(前端有分步进度)。
