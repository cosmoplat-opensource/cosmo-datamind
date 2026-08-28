# 安装部署

面向运维与自建场景。只想先跑起来看效果,请直接看 [QUICKSTART.md](QUICKSTART.md)。

## 环境要求

| 项 | 要求 | 说明 |
|---|---|---|
| Python | 3.10 及以上 | 与 `pyproject.toml` 一致;本轮在 3.13 上通过测试 |
| 操作系统 | macOS / Linux | 未在 Windows 上验证 |
| 磁盘 | 约 200 MB | 含依赖与运行期产物 |
| 可选 | playwright | 仅运行 UI 自动化测试时需要 |
| 网络 | 可选 | 仅在接入大模型端点时需要;本地能力全部离线可用 |

`sqlite3` 命令行工具用于导入示例库,非必需——你也可以直接指向自有的 SQLite 文件。

## 安装

```bash
git clone <repo-url> && cd cosmo-datamind
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` 分三档,文件内已注明:

| 档位 | 组件 | 缺失后果 |
|---|---|---|
| 核心 | flask、requests | 服务起不来 |
| 语义层 | rdflib、pyshacl | 服务正常;SPARQL、标准导出、SHACL 校验返回 503 并提示安装 |
| 按需 | openpyxl、pymysql | 仅在上传 xlsx、实连 MySQL/Doris 时需要 |

未固定版本号:依赖面窄且均为稳定 API。需要可复现构建请自行 `pip freeze`。

## 配置

全部通过环境变量,无配置文件。完整清单见 [`.env.example`](.env.example)。

### 必填（按需）

| 变量 | 作用 | 缺省行为 |
|---|---|---|
| `DATAMIND_DB` | 只读 SQLite 数据源路径 | 指向仓库同级的 `demo_metrics.db`;不存在则相关端点标注"数据库不可用" |

### 接入大模型（可选,但深度问数与 LLM 建本体依赖它）

| 变量 | 说明 |
|---|---|
| `DATAMIND_LLM_BASE` | OpenAI 兼容端点前缀,写到 `/v1` 或 `/v4` 为止,不含 `/chat/completions` |
| `DATAMIND_LLM_KEY` | API Key |
| `DATAMIND_LLM_MODEL` | 模型名 |
| `CLAW_DRIVER` | 设为 `openai` 表示优先使用上述端点 |
| `DATAMIND_LLM_TIMEOUT` | 单轮超时秒数,默认 180 |
| `DATAMIND_LLM_MAX_TOKENS` | 单次回复上限,默认 16384 |

四项前缀为 `DATAMIND_LLM_` 的变量缺任意一项（除超时与上限外）,驱动都不会注册——
系统据实返回"无可用运行时",而不是接受配置后在调用时才失败。

> **推理型模型需放宽超时与额度。** 这类模型的思维链占用同一份 token 预算,
> 也会显著拉长响应时间。默认值（180 秒 / 16384）按 GLM-4.5 实测留有余量;
> 若你的模型更慢,相应调大即可。配得过紧的表现是执行记录中 `llm_plan` 一栏
> 显示"返回 42 字符"随后回退模板——那是被截断,不是模型不会作答。

### 服务参数

| 变量 | 默认 | 说明 |
|---|---|---|
| `DATAMIND_HOST` | `127.0.0.1` | 仅监听本机 |
| `DATAMIND_PORT` | `8092` | |
| `DATAMIND_ENGINE_DIR` | 同级 `ontology-engine` 目录 | 上游本体引擎目录,可选,详见下文 |

## 启动

```bash
python3 server.py          # 前台运行
./start.sh                 # 后台运行,日志写入 workdir/server.log
```

停止:`kill $(lsof -ti :8092)`

## 部署后自检

```bash
curl -s http://127.0.0.1:8092/api/overview      # 数据源状态与本体规模
curl -s http://127.0.0.1:8092/api/ont/runtimes  # 已注册的模型运行时
```

重点看 `current_ready`:

```json
{"current":"openai","current_ready":true,"runtimes":["openai"]}
```

`current_ready` 为 `false` 时,响应中的 `hint` 会写明缺什么。
注意 `runtimes` 列出的是**已注册**的运行时——若本机装有 Claude Code 或 Hermes 的命令行,
即便未接大模型端点,它们也会出现在列表里。判断模型是否接通,以 `current_ready` 为准。

完整回归:

```bash
python3 test_all.py        # 系统级,源码定义 535 个检查点,需服务已启动
python3 test_ui.py         # 全页面走查,需 playwright
python3 test_ui_ops.py     # 浏览器逐步实操,含真实问数;外部端点未配置时显式跳过相关项
```

**测试进程要与服务指向同一个库。** 部分断言在测试进程内直接导入 `server` 求值,
读的是自己环境里的 `DATAMIND_DB`;只给服务端设而漏了测试进程,这些断言会以难以辨识的
方式失败。`test_all.py` 启动时会核对两边并在不一致时直接退出并给出正确命令:

```bash
DATAMIND_DB=$PWD/../demo_metrics.db python3 test_all.py
```

`test_ui_ops.py` 的执行数受数据与外部运行时配置影响。小本体可能被整图召回，
未配置 OpenAI 兼容端点时相关检查会标记为跳过；两种情况都会在测试输出中明确说明。

安装 playwright:`pip install playwright && playwright install chromium`。

## 生产部署

内置 Flask 开发服务器仅供本地使用。对外提供服务请改用 WSGI 服务器并置于反向代理之后:

```bash
pip install gunicorn
gunicorn -w 1 -k gthread --threads 4 -b 127.0.0.1:8092 server:app
```

`-w 1` 是必须的:应用含进程内状态（问数缓存、运行时实例、写锁）,多 worker 会导致状态不一致。
`gthread` 使单一进程能够同时处理多个请求；API 数据源若指向本服务自身，单线程同步 worker
会等待自己而超时。当前共享状态的写入路径已有锁保护，本轮按 4 线程完成了并发与系统回归。
若需横向扩展,应在反向代理层做会话保持,或将状态外置——后者尚未实现。

TLS 与身份认证由反向代理承担。**本服务自身不含用户体系**,请勿在无鉴权的情况下暴露到公网,
详见 [SECURITY.md](SECURITY.md)。

## 上游本体引擎（可选）

一个独立的外部组件,提供多智能体运行时抽象(`engine/agent_runtime.py`)与
会话式构建技能(`web/skills_seed/`)。**不随本仓发布。**

```bash
export DATAMIND_ENGINE_DIR=/path/to/ontology-engine
```

接入后增加的能力:会话式建本体、对话式修改本体(apply / undo)、内置构建技能库,
以及 `hermes`、`claude-code`、`openclaw` 等运行时。

未配置时不会报错:相关端点如实返回"无可用运行时",功能自动降级并在界面标注。

### 关于 OpenClaw

系统调用的是**本机已安装的 `openclaw` 命令行**,会话文件落在 `~/.openclaw/` 下。
OpenClaw 连接哪个网关（`wss://…` 与 Token）在 OpenClaw 客户端内配置,
本服务不直接建立网关连接。因此顺序是:先在 OpenClaw 侧确认网关可用,
再设置 `DATAMIND_ENGINE_DIR` 与 `CLAW_DRIVER=openclaw`。

## 数据与产物落盘位置

| 路径 | 内容 | 是否可删 |
|---|---|---|
| `workdir/*.json` | 本体 IR、编辑日志、动作记录、评测结果 | 删除即丢失构建成果 |
| `workdir/uploads.db` | 上传文件解析后的表 | 可删,重新上传即可 |
| `workdir/server.log` | `start.sh` 的运行日志 | 可删 |
| `workdir/qa_skills.json` | 沉淀的问数技能 | 可删,失去已沉淀的 SQL 复用 |

数据源(`DATAMIND_DB`)以只读方式打开,系统不会写入其中。

标准格式导出(TTL / JSON-LD / RDF-XML)为流式响应,不在服务端落盘,由浏览器直接下载。

## 升级

```bash
git pull && pip install -r requirements.txt
```

`workdir/` 不随代码更新,已建本体会保留。若某次升级改变了 IR 结构,
决策记录(`specs/decisions/`)中会写明迁移方式。

## 常见问题

**端口被占用** — `lsof -ti :8092` 查出占用进程,或改用 `DATAMIND_PORT`。

**`/api/overview` 返回 `warning: 数据库不可用`** — `DATAMIND_DB` 路径不存在。
这是如实标注而非故障,图谱浏览等不依赖数据库的功能照常可用。

**`current_ready` 为 false** — `CLAW_DRIVER` 指定的运行时未注册。
接 OpenAI 兼容端点时,`DATAMIND_LLM_BASE` 与 `DATAMIND_LLM_KEY` 缺一不可,
端点前缀写到 `/v1` 或 `/v4` 为止。具体缺什么见响应里的 `hint`。

**页面显示的与刚改的不一致** — 页面每 60 秒自检一次版本,发现服务端文件已更新会提示刷新。
长时间开着的标签页请手动刷新。
