# DR-073 · 深度问数应用契约兼容

- **状态 / Status**：proposed（待方案评审）。
- **日期 / Date**：2026-11-12。
- **关联 / Refs**：[IR-027](../iterations/IR-027-deepqa-application-compatibility.md)、[总方案](../../docs/datamind-application-contract-design.md)。

## 上下文 / Context

现有 _qa_graph_profile、_qa_scope_error、_qa_validate_sql 和 metric_contract 已提供数据可查询性与执行前控制；还没有统一应用注册及修复报告。

## 决定 / Decision

以下为建议方案，用户方向已明确，具体接口和取舍仍待评审。

1. 数据本体构建成功直接入库，不增加应用选择或契约检查步骤。用户从现有入口发起问数请求时，由后端自动绑定 data.deep_qa 契约并适配现有 JSON IR；不把数据本体强制转换成机器人 OWL，也不套用 Skill／ParameterBinding。

2. 结构预检复用对象到表／列绑定、当前查询连接目录及可用表范围。部分表不可用不全局禁止问数：保留可查询子集，在具体 SQL 用到不可用表时阻断。普通单表问题不强制具备指标契约。

3. 任务阶段是具体问题及生成计划的检查；SQL 仍经过单语句只读、本体锚定、列／连接／指标口径等既有关卡。metric_contract 是应用中的指标子契约，保留 certified、verified、candidate、heuristic 等原消费差异及 MCP 规则。

4. 同步 /api/chat 与 SSE /api/chat/stream 使用同一预检服务，SSE 以结构化缺项事件结束；已有请求字段、事件顺序和响应字段兼容，新字段增量加入。

5. 保留未选图谱时 demo 默认、既有离线规划及有记录的回退。旧不存在图谱的 fallback 暂保持并显式警告；被删除资产必须明确拒绝，不得经 legacy key 复活或回退 demo。

6. 旧 graph key 解析为有效 IR 的冻结快照，包含人工 edits、选中多图有序向量及连接目录指纹。先支持已有注册快照，未登记图只读解析，不因预检 GET 隐式建库。

7. 新鲜性检查先于缓存返回；缓存键包括有效载荷／编辑摘要、契约版本、连接和资产生命周期修订。数据连接变更与删除使缓存失效；每条 SQL 执行前仍复核。

## 后果 / Consequences

依赖 IR-025；以影子检查比较现有结果，再启用新增入口诊断；保留最终 SQL 守卫。
需要版本化检查和修复记录，增加维护成本；换取构建自由、应用诊断和执行一致性。
不改变 IR-024 已交付范围，按新迭代实现并回归。
