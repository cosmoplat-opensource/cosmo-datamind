# Cosmo DataMind

**数据治理 × 工业本体 × 深度问数** 的一体化原型:把杂乱的业务库,建成一张**带类型、带口径、带证据**的工业本体图谱,并让人和 Agent 都能可信地问数、诊断、执行动作。

Apache-2.0 · Python + Flask + 原生 JS(无前端构建步骤)· 单端口本地运行

---

## 它解决什么问题

大模型接入企业数据时,常见的不是"模型不够强",而是**语义对不上**:

- 同一个"工单号",6 张表里 5 种列名,表间关系一条都没写进库;
- "直通率"和"良率"被混用,答案给了数却给不出口径;
- 跨表 JOIN 全靠猜,错了也没人发现。

Cosmo DataMind 的思路是**先把语义底座建对**:用大模型广撒网提议、用**真实数据裁决真伪**、用国际标准把关、由人做最终定夺,产出一张每条关系都带证据与状态的本体图谱;再让问数、诊断、动作都跑在这张图谱上。

## 核心特性

| 能力 | 说明 |
|---|---|
| **半自动本体构建** | 机器提议 → 数据裁决 → 标准把关 → 人工定夺。关系靠取值重叠(≥60%)与父键近唯一(≥0.95)自证,**无据一律标 candidate,绝不冒充 verified** |
| **证据分层** | 每条关系带状态 `verified` / `asserted` / `candidate` / `gap`;图上线型即语义,一眼看出哪条敢用 |
| **深度问数** | 自然语言 → 本体定位对象与口径 → 生成 SQL → **只读执行** → 带出处作答;同屏给出指标口径卡(计算口径/数据出处/血缘) |
| **口径门禁** | SQL 执行前校验:表须在本体白名单内,JOIN 键须落在已验证关系上,越界即拦 |
| **根因诊断** | 沿本体关系两跳召回,输出受本体边界约束的根因与检查清单;实体未命中即**如实拒答**,不作无锚定生成 |
| **动作层** | 类型化参数 + 风险分级:低风险直执行、高风险人审批,决策全程留痕。经 MCP 对外时,**发起动作是唯一写工具,审批不开放** |
| **标准导出** | OWL2 / RDF / SHACL / SKOS / JSON-LD,并提供 SPARQL 端点 |
| **问数评测** | 金标题集 × 三组同题对照(无检索增强 / 图谱增强 / 本体增强),度量正确率、出处引用率与口径拦截数 |

## 安装与部署

以下命令序列均经全新克隆 + 干净 venv 实测。

### 1. 安装

```bash
git clone <repo-url>
cd cosmo-datamind
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置(可选)

```bash
cp .env.example .env               # 按需填写;全部留空也能启动
export DATAMIND_DB=/path/to/your.db          # 只读 SQLite 数据底座
export DATAMIND_ENGINE_DIR=/path/to/ontology-engine   # 上游引擎(可选)
```

两者都不设也能起服务:图谱页直接浏览内置示例本体(108 对象 / 60 关系),
需要数据库的端点会在响应里如实标注「数据库不可用」,不会静默失败。

### 3. 启动

```bash
python3 server.py                  # 前台运行 → http://127.0.0.1:8092
# 或
./start.sh                         # 后台运行,日志在 workdir/server.log
```

停止:`kill $(lsof -ti :8092)`

### 4. 验证与测试

```bash
curl http://127.0.0.1:8092/api/overview      # KPI 概览(无库时含 warning 字段)
python3 test_all.py                          # 系统级回归(需服务已启动)
```

### 5. 生产部署(可选)

内置的 Flask 开发服务器仅供本地使用。对外提供服务时用 WSGI 服务器 + 反向代理:

```bash
pip install gunicorn
gunicorn -w 1 -b 127.0.0.1:8092 server:app   # 已实测;-w 1:应用含进程内状态,勿多 worker
```

再由 Nginx 等反代承担 TLS 与鉴权——本服务自身不含用户体系,见 [SECURITY.md](SECURITY.md)。

## 运行形态:两档

本系统分**独立运行**与**接引擎运行**两档,差别只在需要大模型的环节:

| 功能 | 独立运行(默认) | 接上游引擎 |
|---|---|---|
| 图谱浏览 / 指标 / 数据质量 / 术语 / 动作中心 | ✅ | ✅ |
| SQL 工作台(只读) / SPARQL / 标准导出 | ✅ | ✅ |
| 深度问数 | ✅ 模板兜底,出真实数据 | ✅ LLM 生成 SQL 计划 |
| 本体构建 | ✅ 纯数据驱动(`quick_build`) | ✅ 多模态 LLM 提议 + 裁决 |
| 对话式本体编辑(apply/undo) | ❌ 需要引擎 | ✅ |
| 内置构建技能库 | ❌ 需要引擎 | ✅ |

上游本体引擎是**可选组件,不随本仓发布**——本仓开源范围仅 Cosmo DataMind 本体。
若你拥有兼容的引擎目录(提供 `engine/agent_runtime.py` 多运行时抽象与 `web/skills_seed/` 构建技能),
一个环境变量即可接入:

```bash
export DATAMIND_ENGINE_DIR=/path/to/ontology-engine
```

**未配置时不会报错**——相关端点如实返回"无可用运行时",功能自动降级并在界面标注,
不会静默失败,也不会用兜底数据冒充真实结果。

## 配置

全部通过环境变量,见 [`.env.example`](.env.example)。要点:

- `DATAMIND_DB` — 只读数据底座路径
- `DATAMIND_ENGINE_DIR` — 上游本体引擎目录(可选)
- `DATAMIND_HOST` / `DATAMIND_PORT` — 默认 `127.0.0.1:8092`
- `OPENAI_API_KEY` 等模型密钥 — **环境变量优先于配置文件**,界面只回显掩码

## 安全

默认**只监听本机**,不含用户体系与鉴权。请勿在无反向代理鉴权的情况下暴露到公网。
已内置的防护(只读三重防写、CSRF 守卫、路径穿越校验、标识符消毒、SPARQL 禁外呼)
与已知边界,见 [SECURITY.md](SECURITY.md)。

## 方法论文档

`specs/` 下是完整的**规约驱动开发(SDD)**记录——不是事后补的说明,而是开发时的决策依据:

- `specs/decisions/` — 23 篇决策记录(DR),每篇写清背景、选项、取舍与代价
- `specs/iterations/` — 6 篇迭代记录(IR),含验收标准与实测结果
- `specs/map.md` — 入口索引

半自动构建方法论的核心取舍(为什么数据裁决优先于 LLM 判断、为什么人审只产生 `asserted`)
都记在 `DR-002` 与 `DR-011` 里。

## 测试

```bash
python3 server.py &            # 先起服务
python3 test_all.py            # 系统级回归(279 断言)
python3 test_ui.py             # 全 UI 走查(可选;需 pip install playwright && playwright install chromium)
```

套件覆盖路由可达性、只读约束、CSRF、路径穿越、证据分层一致性、编辑回放等。
**未配置引擎时,依赖引擎的用例会失败**——这是如实反映能力边界,不是缺陷。

## 贡献

欢迎 issue 与 PR。改动请附上对应的 DR/IR 说明,并保证 `test_all.py` 不出现新的失败项。
安全问题请勿公开提交,见 [SECURITY.md](SECURITY.md)。

## 许可证

[Apache-2.0](LICENSE)。第三方组件署名见 [NOTICE](NOTICE)。
