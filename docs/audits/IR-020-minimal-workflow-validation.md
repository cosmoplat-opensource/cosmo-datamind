# IR-020 · 基础任务流程审计 — DR-066

## T01：范围变更与代码复核（2026-11-05）

基线为 dyh_dev 的 4809a0b，IR-019 T07 已 GREEN，整轮 T08 尚未验收。
用户提出界面过于繁琐，随后明确选择“先打通构建到行为树生成，暂缓高级维护功能”。
按此选择记录 DR-066 和 IR-020，不将其解释为原复杂界面的验收确认。

| 实际读取的位置 | 结论 |
|---|---|
| `/home/dyh/workspace/Project/yang_implementation/归档/ontology/README.md` | CLI 构建 OWL，Protégé 维护知识，没有当前 DataMind 的五个治理页签 |
| 同目录 `agent/README.md`、`agent/web/index.html`、`agent/web/app.js` | 页面核心是任务对话与本次结果；澄清期间不展示旧生成结果；模型缺配置明确提示 |
| `agent/llm_client.py` | 完整角色消息、JSON 模式、六字段校验与最多一次格式修复；不是单条自然语言直出 XML |
| `agent/trusted_agent.py` | 重放用户明确提及，处理否定、撤销、换上游目标；知识缺口与任务歧义分开 |
| DataMind `srv_engine.py`、`openai_runtime.py` | 可复用模型配置；普通运行时不能直接替代受约束 JSON 解析器 |
| `domains/robotics/generation.py`、`grounding.py` | 已有严格编译验证；不应重新实现生成器 |
| `ontology_assets/store.py`、`domain_runs/robotics.py`、`domain_runs/store.py` | 现有公开加载要求已发布；需要独立候选应用快照入口，运行/产物持久化可以复用 |
| IR-019 与 `ui/modules/robotics-review*.js` | 已实现高级治理，用户当前要求调整默认体验和交付顺序 |

### 用户可见的变化目标

从“构建 → 维护知识 → 证据/版本/审核 → 发布 → 尚未接入任务生成”改为“构建或导入 → 描述任务 → 澄清 → 查看/下载”。
页面只显示一个当前版本；不要求填写操作者署名、操作理由、修改依据，也不展示工作头和编辑底本。
本体知识不足的处理保持诚实：说明缺什么，支持下载本体外部修改再导入；不自动确认或伪造物理数据。
高级治理后端不删除，原复杂 UI 的验收暂缓。

### 本轮未改动的范围

不改用户 `.env`、现有运行目录、数据库及其本体资产，不启动真实模型调用。
保留原未提交内容；Proposal 与 map 只加本次优先级说明，不整体覆盖或顺手提交原有草稿。
T01/T02 不修改生产实现和旧测试，当前页面行为尚未变化。

## T02：任务解析与明确选择 RED

本批不是整条构建到生成链路验收；服务/HTTP/浏览器联动由 T04/T05 承接。

| 测试文件 | 数量 | 核对内容 |
|---|---:|---|
| tests/unit/test_robotics_task_model.py | 30 | 复用平台配置与按任务选模、完整角色消息与 JSON 模式、六字段类型/长度、一次格式修复、拒答/截断/连接失败、缺配置 |
| tests/unit/test_robotics_task_dialogue.py | 15 | 业务目录、别名、模型补猜无效、缺目标、否定/撤销/模糊/同名选择、改上游目标后清下游、明确回复解除歧义 |

命令：

```bash
.venv/bin/python -m pytest tests/unit/test_robotics_task_model.py tests/unit/test_robotics_task_dialogue.py --tb=short
```

实际结果：45 项失败，0 项通过，0 收集错误，0 跳过，退出码 1。
两种失败原因均是 ModuleNotFoundError：生产模块 domains.robotics.task_model / task_dialogue 尚未新增。
测试在函数内部导入实现，故能完整收集并逐项呈现失败，而非导入错误导致整文件无法审查。
这符合 TDD RED，不能称为已有功能被破坏，也不代表新增功能已经实现。

辅助检查：tests/meta 21 项通过；两个新增文件的 ruff、mypy 退出码均为 0。
IR-019 登记的 134 个已审文件、10 个冻结生产文件摘要均一致。
未跑全量回归：本批刻意保持新增行为 RED，完整回归在实现后执行。

结构化证据见 [IR-020-t02-red-results.json](IR-020-t02-red-results.json)，含实际命令、结果、日志摘要和新测试摘要。
本地运行日志在 `.check-results/ir020-t02/`。

### 本次请审查的覆盖范围

1. 模型沿用平台配置，错误时明确失败；不会偷偷使用默认抓移放任务顶替失败结果。
2. 模型只能给出六字段结构化任务；缺目标不会因目录只有一个选项而自动补齐。
3. 用户否定、撤销、改工位/载具后，旧选择会失效；同名对象须明确区分。
4. 以上均为模型传输替身测试，尚不证明在线模型准确率、存储/HTTP 或页面联动。

本批不修改已审测试或实现；覆盖确认后按 SDD 第⑥步进入 T03。
真实 HTTP 传输、会话绑定/恢复、旧入口调整、OWL 导入、完整构建到生成及预览下载的浏览器测试在 T04 单独审查。
