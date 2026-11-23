# 安装部署

面向自行部署和运维的读者。只想先试用,请看 [QUICKSTART.md](QUICKSTART.md)。

## 环境要求

| 项 | 要求 | 说明 |
|---|---|---|
| Python | 3.10 及以上 | 与 `pyproject.toml` 一致;本轮在 3.13 上通过测试 |
| 操作系统 | macOS / Linux | 未在 Windows 上验证 |
| 磁盘 | 约 200 MB | 含依赖与运行期产物 |
| 可选 | playwright | 仅运行 UI 自动化测试时需要 |
| 网络 | 可选 | 只在接入大模型端点时需要;其余功能离线可用 |

`sqlite3` 命令行工具用于导入示例库,不是必需的;也可以直接使用已有的 SQLite 文件。

## 安装

```bash
git clone <repo-url> && cd cosmo-datamind
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` 分三档,文件内已注明:

| 档位 | 组件 | 缺失后果 |
|---|---|---|
| 核心 | flask、requests、gunicorn | 服务或生产 WSGI 入口无法启动 |
| 语义层 | rdflib、pyshacl | 服务正常;SPARQL、标准导出、SHACL 校验返回 503 并提示安装 |
| 按需 | openpyxl、pymysql、psycopg2-binary | 仅在上传 xlsx、实连 MySQL/Doris/PostgreSQL 时需要 |
| 按需 | cryptography | 加密保存外部库的连接凭据;未安装时凭据以明文保存(仅属主可读)并告警 |
| 按需 | JDK 11+、Hive JDBC 驱动(standalone jar) | 仅在实连 Hive 或 Spark Thrift Server 时需要;设 `DATAMIND_HIVE_JDBC_CLASSPATH` 指向驱动,`java` 不在 PATH 时设 `DATAMIND_JAVA`。查询经 `drivers/HiveJdbcBridge.java` 执行,表清单只读 JDBC 元数据 |

`requirements.txt` 只规定版本下限。需要可复现的安装时,使用固定了版本的 `requirements-dev.txt`。

## 配置

通过环境变量配置,`./start.sh` 会读取仓库根目录的 `.env`;在界面上保存的引擎配置写入 `workdir/engine_config.json`。
完整清单见 [`.env.example`](.env.example)。

### 必填（按需）

| 变量 | 作用 | 缺省行为 |
|---|---|---|
| `DATAMIND_DB` | 只读 SQLite 数据源路径 | `python3 server.py` 默认指向仓库上一级目录的 `demo_metrics.db`,`./start.sh` 默认指向仓库内的 `examples/demo_metrics.db`;文件不存在时,相关接口标明"数据库不可用" |
| `DATAMIND_ROBOTICS_ENABLED` | 机器人构建与应用功能开关 | 默认开启；设为 `0` 或 `false` 可关闭，修改后重启服务 |

### 接入大模型（可选;深度问数和借助模型建本体需要）

| 变量 | 说明 |
|---|---|
| `DATAMIND_LLM_BASE` | OpenAI 兼容端点前缀,写到 `/v1` 或 `/v4` 为止,不含 `/chat/completions` |
| `DATAMIND_LLM_KEY` | API Key |
| `DATAMIND_LLM_MODEL` | 模型名 |
| `CLAW_DRIVER` | 设为 `openai` 表示优先使用上述端点 |
| `DATAMIND_LLM_TIMEOUT` | 单轮超时秒数,默认 180 |
| `DATAMIND_LLM_MAX_TOKENS` | 单次回复上限,默认 16384 |

`DATAMIND_LLM_BASE` 和 `DATAMIND_LLM_KEY` 缺任何一项,驱动都不会注册,系统返回"无可用运行时"。
`DATAMIND_LLM_MODEL` 不设时使用驱动的默认模型名,建议显式设置。

> **推理型模型需要放宽超时和回复上限。** 这类模型的思考内容占用同一份 token 额度,
> 响应时间也更长。模型更慢时相应调大默认值（180 秒、16384）。设置过小的表现是:执行记录里 `llm_plan` 一栏
> 只返回几十个字符,随后改用内置模板,原因是回复被截断。

### 服务参数

| 变量 | 默认 | 说明 |
|---|---|---|
| `DATAMIND_HOST` | `127.0.0.1` | 仅监听本机 |
| `DATAMIND_PORT` | `8092` | |
| `DATAMIND_ENGINE_DIR` | 上一级目录的 `ontology-engine` | 上游本体引擎目录,可选,见下文 |

## 启动

```bash
python3 server.py          # 本地开发：Flask 开发服务器,前台运行
./start.sh                 # 本机部署：读取 .env,以 Gunicorn 在后台常驻运行,日志在 .deploy/
```

查看状态:`./status.sh`;停止:`./stop.sh`。

## 部署后自检

```bash
curl -s http://127.0.0.1:8092/api/overview      # 数据源状态与本体规模
curl -s http://127.0.0.1:8092/api/ont/runtimes  # 已注册的模型运行时
```

重点看 `current_ready`:

```json
{"current":"openai","current_ready":true,"runtimes":["openai"]}
```

`current_ready` 为 `false` 时,响应中的 `hint` 写明缺什么。
`runtimes` 列出的是已注册的运行时:本机装有 Claude Code 或 Hermes 的命令行时,
即使没有接入大模型端点,它们也会出现在列表里。模型是否接通以 `current_ready` 为准。

完整回归:

```bash
python3 test_all.py        # 系统级,源码定义 548 个检查点,需服务已启动
python3 test_ui.py         # 全页面走查,需 playwright
python3 test_ui_ops.py     # 浏览器逐步实操,含真实问数;外部端点未配置时显式跳过相关项
```

**测试进程要和服务使用同一个库。** 部分检查在测试进程内直接导入 `server` 求值,
读取的是测试进程自己的 `DATAMIND_DB`。只给服务设了这个变量时,这些检查的失败原因不容易看出来。
`test_all.py` 启动时会核对两边,不一致时退出并给出正确的命令,例如:

```bash
DATAMIND_DB=$PWD/examples/demo_metrics.db python3 test_all.py
```

`test_ui_ops.py` 的执行数受数据与外部运行时配置影响。小本体可能被整图召回，
未配置 OpenAI 兼容端点时相关检查会标记为跳过；两种情况都会在测试输出中说明。

安装 playwright:`pip install -r requirements-ui.txt && python -m playwright install chromium`。

## 生产部署

内置 Flask 开发服务器仅供本地使用。生产环境使用仓库内的专用 WSGI 入口和受测配置：

```bash
gunicorn -c gunicorn.conf.py wsgi:application
```

`gunicorn.conf.py` 固定 `workers=1`：应用含进程内状态（问数缓存、运行时实例、作业表、写锁），
多 worker 会导致状态不一致。`gthread` 以默认 8 个有界线程提供并发；API 数据源若指向本服务
自身，单线程同步 worker 会等待自己而超时。长构建与 SSE 的 worker 超时默认为 1800 秒，可通过
`DATAMIND_GUNICORN_THREADS` 与 `DATAMIND_GUNICORN_TIMEOUT` 在受限范围内调整。
若需横向扩展,应在反向代理层做会话保持,或将状态外置——后者尚未实现。

Nginx 示例见 [`deploy/nginx/cosmo-datamind.conf`](deploy/nginx/cosmo-datamind.conf)：它把 Gunicorn
留在 `127.0.0.1:8092`，保留原始 Host/转发头，关闭代理缓冲以支持 SSE，并把上传上限对齐为
25 MiB。模板不包含证书和认证配置；上线前必须补上 TLS 和身份认证。**本服务自身不含用户
体系**,请勿在无鉴权的情况下暴露到公网,详见 [SECURITY.md](SECURITY.md)。

## 上游本体引擎（可选）

一个独立的外部组件,提供多智能体运行时抽象(`engine/agent_runtime.py`)与
会话式构建技能(`web/skills_seed/`)。**不随本仓发布。**

```bash
export DATAMIND_ENGINE_DIR=/path/to/ontology-engine
```

接入后可以使用会话式本体构建、上游引擎自带的构建技能,以及 `hermes`、`claude-code`、`openclaw` 等运行时。

未配置时,相关接口返回"无可用运行时",对应功能改用不依赖模型的方式运行,并在界面上标明。

### 关于 OpenClaw

系统调用的是本机已安装的 `openclaw` 命令行,会话文件保存在 `~/.openclaw/` 下。
OpenClaw 连接哪个网关（`wss://…` 与 Token）在 OpenClaw 客户端内配置,
本服务不直接连接网关。配置顺序:先在 OpenClaw 里确认网关可用,
再设置 `DATAMIND_ENGINE_DIR` 与 `CLAW_DRIVER=openclaw`。

## 数据与产物的存放位置

| 路径 | 内容 | 是否可删 |
|---|---|---|
| `workdir/*.json` | 本体 IR、编辑日志、动作记录、评测结果 | 删除即丢失构建成果 |
| `workdir/uploads.db` | 上传文件解析后的表 | 可删,重新上传即可 |
| `.deploy/access.log`、`.deploy/error.log` | `./start.sh` 启动的 Gunicorn 的日志 | 可删 |
| `workdir/qa_skills.json` | 保存的问数技能 | 可删,已保存的 SQL 不再复用 |

数据源(`DATAMIND_DB`)以只读方式打开,系统不会写入其中。

标准格式导出(TTL / JSON-LD / RDF-XML)直接返回给浏览器下载,不在服务端保存。

## 升级

```bash
git pull && pip install -r requirements.txt
```

`workdir/` 不随代码更新,已建本体会保留。若某次升级改变了 IR 结构,
决策记录(`specs/decisions/`)中会写明迁移方式。

## 常见问题

**端口被占用** — `lsof -ti :8092` 查出占用进程,或改用 `DATAMIND_PORT`。

**`/api/overview` 返回 `warning: 数据库不可用`** — `DATAMIND_DB` 路径不存在。
图谱浏览等不依赖数据库的功能不受影响。

**`current_ready` 为 false** — `CLAW_DRIVER` 指定的运行时未注册。
接 OpenAI 兼容端点时,`DATAMIND_LLM_BASE` 与 `DATAMIND_LLM_KEY` 缺一不可,
端点前缀写到 `/v1` 或 `/v4` 为止。具体缺什么见响应里的 `hint`。

**页面内容与刚做的修改不一致** — 页面每 60 秒检查一次版本,发现服务端文件已更新时提示刷新。
长时间打开的标签页请手动刷新。
