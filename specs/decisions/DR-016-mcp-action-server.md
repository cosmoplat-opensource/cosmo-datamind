# DR-016 动作层 MCP server:invoke_action 唯一写工具,审批语义留在服务端

日期:2026-07-25 · 状态:已实现 · 回归:test_all.py 119/119

## 决定

新增 `mcp_action_server.py`(MCP stdio,零依赖,逐行 JSON-RPC 2.0),把动作中心(DR-015)暴露给任意 MCP 客户端(Claude Code / hermes / 外部 Agent),对齐 deck P23「MCP 市场」与 P24「最小依赖(MCP)」:

- 工具面(刻意极小):`list_actions`(读目录)· **`invoke_action`(唯一写)** · `get_action_status`(读跟踪)。
- **审批(approve/deny)故意不暴露** —— 批准是人的专属入口(动作中心页面)。任何 Agent 接入即自动继承治理:只能提议改变、不能批准改变;高风险动作 invoke 后只会 pending,Agent 拿到的回执明确写着"必须由人批准,无法绕过"。
- 薄代理架构:工具调用全部转发 DataMind HTTP API(参数校验/风险分级/审批队列/审计的**单一实现**留在 server.py),MCP 层不复制任何治理逻辑 —— 双入口(网页/Agent)永不漂移。
- stdout 只出协议消息,日志走 stderr;`DATAMIND_URL` 可配(默认 :8092)。

## 接入

`claude mcp add datamind-actions -- python3 <绝对路径>/mcp_action_server.py`(其它 MCP 客户端同理:command=python3, args=[该文件])。

## 验证(实测协议往返)

initialize/tools/list ✓;list_actions 出目录 ✓;低风险 report_repair invoke→executed ✓;高风险 adjust_delivery invoke→pending(回执声明须人批)✓;缺必填→isError,服务端校验原样透传 ✓;**人在界面批准后,Agent 经 get_action_status 看到 executed+审批人+意见+效果** —— 人机治理闭环成立 ✓。

## 边界

- MCP 层无鉴权(stdio 本地信任模型);对外网暴露需先加认证层。
- operator 为 Agent 自报身份,审计里与人类操作人同列,建议 Agent 用 `agent-<名>/<会话>` 命名约定。
