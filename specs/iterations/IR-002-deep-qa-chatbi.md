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

## 历史会话回归 QA-HISTORY-01

在基线 `f37daaf` 的隔离服务和 Chromium 中，从发送按钮连续完成两轮问数。同一浏览器、同一数据源下，正常刷新后能从历史栏打开两轮问题和答案正文。以下四项仍待修复。

| 操作与现象 | 原因 | 修复方向 |
| --- | --- | --- |
| 答完后切到本体库再返回，历史栏仍显示“暂无历史” | `dqRenderHist()` 排除当前实时会话，页面导航保留了实时状态 | 返回问数页时让已完成的会话在历史栏可见，同时保留当前对话的续问上下文 |
| 浏览器存储已满时，新答案可见，刷新后只剩旧会话 | `dqSaveConv()` 向 `localStorage` 写入整组快照，忽略写入异常 | 保留已存历史。新会话写入失败时尝试备用存储，或在用户离开前明确提示 |
| 刷新时指标接口返回 503，已保存的会话也无法打开 | `chatInit()` 把错误消息当指标数组调用 `filter()`，在绘制历史前中断 | 让本地历史先显示，指标加载失败只影响即时问数的指标选择 |
| 打开历史后，原答案依据卡里的结果编号消失 | `ask()` 保存的快照省略了 `evidence_card`，回放时没有原始依据卡可用 | 保存回放需要的依据与原结果编号，并兼容已有快照 |

上述五条用例从真实页面发起问数并通过界面检查回放。单独运行 `python -m pytest tests/frontend/qa_history_browser.py`，或通过 `python scripts/check.py --suite ui` 执行。[浏览器回归用例](../../tests/frontend/qa_history_browser.py)
