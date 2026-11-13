# ATH 接入：DataMind 作为受保护的服务提供方

本目录让外部智能体经 [ATH（智能体可信握手协议）](https://github.com/ath-protocol) 的网关访问 DataMind。
DataMind 本身不改、只监听本机；本适配层是对智能体开放的唯一入口，负责用户侧授权和按范围放行。

```
智能体 ──ATH 握手──▶ ATH 网关 ──OAuth 2.0──▶ 适配层 (8093) ──本机──▶ DataMind (8092)
```

## 适配层做什么

- **授权服务器**：授权页（DataMind 用户登录并勾选授予的范围）、一次性授权码、PKCE S256、令牌端点、RFC 8414 元数据。
- **资源服务器**：每个接口对应一个范围，令牌里没有该范围即拒绝；未列出的接口一律 404。
- **MCP 桥接**：`POST /mcp` 转给 `mcp_build_server.py`。构建作业由 DataMind 服务端持有（DR-101），
  桥接进程结束不影响构建，之后凭作业号可查。
- **A2A**：`POST /a2a`（A2A 0.3.0 的 `message/send`）和 AgentCard。
- **审计**：每次授权、发令牌、放行和拒绝追加写入 `audit.jsonl`。

## 范围与接口

| 范围 | 允许的访问 |
|---|---|
| `datamind:catalog.read` | `GET /api/sysinfo`、`/api/tables`、`/api/overview`、`/api/glossary`；A2A 技能 `catalog.tables` |
| `datamind:ontology.read` | `GET /api/graphs`、`/api/graph/<key>`、`/api/metrics`；A2A 技能 `ontology.graphs` |
| `datamind:sparql.query` | `POST /api/sparql` |
| `datamind:build.read` | MCP 的只读工具（数据源、技能、构建产物、作业进度、验收、人审队列）；A2A 技能 `build.list` |
| `datamind:build.run` | MCP 的 `start_build`（唯一的写操作） |
| `datamind:qa.ask` | `POST /api/chat`（深度问数，只读查询；响应里的 `degraded` 标明是否为引擎未响应时的内置模板结果） |

**数据来源随令牌限定。** 用户在授权页上选一个数据来源，令牌只对这一个来源有效：
适配层转发每个请求时带上它，调用方自己带的来源参数被丢弃；经 MCP 发起的构建只能建在这个来源上。

**请求号贯通。** 每个放行的请求生成一个请求号，写进适配层的审计日志，并随请求带给 DataMind。
经 ATH 发起的构建，其作业记录和图谱的构建记录里有同一个请求号和调用方（`ath:<客户端>:<用户>`），
在图谱的审计视图（`/api/ont/audit/<key>` 的 `builds`）里可以查到。

没有开放的能力：评审与编辑（确认、否决关系，改动词，确认指标计算规则）、删除图谱、引擎与连接配置、取消构建。
评审与编辑、删除、配置只能由人在 DataMind 界面里做。继续构建不会移出或改写人工决定过的关系（DR-097），
所以拿到 `datamind:build.run` 的智能体也改不了人审结论。

## 运行

```bash
./start.sh                                   # DataMind，8092
python3 -m ath_adapter.provider              # 适配层，8093
```

| 环境变量 | 缺省 | 含义 |
|---|---|---|
| `DATAMIND_URL` | `http://127.0.0.1:8092` | DataMind 地址 |
| `DATAMIND_ATH_PORT` | `8093` | 适配层端口（只监听 127.0.0.1） |
| `ATH_GATEWAY_URL` | `http://localhost:3000` | ATH 网关地址，写进 AgentCard |
| `ATH_GATEWAY_CALLBACK` | `http://localhost:3000/ath/callback` | 首次启动时登记的回调地址 |
| `DATAMIND_ATH_STATE` | `workdir/ath_state` | 状态目录：OAuth 客户端密钥、用户口令哈希、初始口令、审计日志，文件权限 0600，不入库 |
| `DATAMIND_ATH_ORGANIZATION` | `COSMO DataMind` | AgentCard 里的组织名 |

首次启动生成网关用的客户端密钥和一个用户 `analyst`，初始口令写在状态目录的 `initial_password.json`。
授权码和令牌只在内存里，重启适配层后失效。

## 测试

- 单元测试 `tests/unit/test_ath_adapter.py`：不需要网关和运行中的 DataMind，随 `scripts/check.py --suite unit` 运行。
- 端到端 `ath_adapter/e2e/run_e2e.py`：需要 DataMind 在 8092、ATH 参考网关的本地检出（`ATH_GATEWAY_DIR`）、
  PyJWT，以及官方 Python SDK（`ATH_SDK=official`，缺省）或自带客户端（`ATH_SDK=own`）。
  报告写到 `.check-results/ath/`。它不在常规检查里。

## 已知限制

- 一个令牌只对应一个数据来源；要访问另一个来源需要重新授权。只有 SQLite 来源可选。
- 调用方和请求号由适配层带给 DataMind，DataMind 自身不验证；绕过适配层直接访问 8092 的调用方可以伪造它们。
- 审计日志仍是两份文件（适配层的 `audit.jsonl` 与 DataMind 的作业、构建记录），靠请求号对账，没有合并成一份。
- 深度问数走同步接口，单次最长等待 240 秒；流式过程不经适配层转发。
- 令牌是不透明字符串、存在内存里，适配层只能单进程运行。
