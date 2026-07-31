# IR-002 · 深度问数对齐平台 chat-bi(一模一样甚至更好)

- **状态**: delivered
- **关联**: [[DR-004-deep-qa-sse]]、[[DR-001-local-readonly-execution]]、[[DR-003-runtime-neutral-naming]]

## 目标 / Goal
复现并超过平台 `bigdata/chat-bi·deep`:实时执行记录、渐进出图、数据源&技能选择、上传可查、沉淀技能、历史对话;
引擎慢/限流/离线仍可用,不返回陈旧/串会话结果。

## 交付 / Deliverables
- [x] 三模式(即时秒查 / 深度 / 智能报告);SSE 流式 22 步执行记录 + 渐进出图(`/api/chat/stream`)。
- [x] 数据源&技能弹窗;按图谱选源实际生效(解析绑定表并入 focus)。
- [x] 文件上传可查(`up.<表>` 只读挂载);缓存键含上传指纹防陈旧。
- [x] 沉淀为 Skill(`/api/chat/save_skill`)、历史对话(localStorage)、Markdown 洞察、复制/反馈。
- [x] 引擎离线/限流走模板兜底 + 规则化摘要;执行记录引擎名中性化。

## 任务 / Tasks
1. `chat_stream` SSE 生成器 + 后台线程轮询 plan_steps + 心跳。
2. `agent_sql_plan`/`narrative_llm` 多引擎兜底 + `_looks_like_error` 识别;`fallback_plan`/`_rule_summary`。
3. 前端 `ask()` 流式读取 + 渐进渲染 + 中止/复位 + 沉淀/历史。

## 验收 / Acceptance
- 实测:毛利问题 → 3 条 SQL 本地执行 17 期真数(毛利率 20.7→13.7→9.8%),与平台 ground truth 吻合。
- SSE 崩溃防护(BLOB→`default=str` 不中断);中止后按钮复位;缓存 0s 秒回不串源/上传。
- `test_all.py` 深度问数相关断言通过。
