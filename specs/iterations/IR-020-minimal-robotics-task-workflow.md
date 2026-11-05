# IR-020 · 基础版：构建本体到行为树生成

- **状态 / Status**：in-progress。
- **日期 / Date**：2026-11-05。
- **关联 / Refs**：[DR-066](../decisions/DR-066-minimal-robotics-task-workflow.md)、DR-060、DR-061、DR-062、DR-064。
- **范围来源**：用户明确选择“先打通构建到行为树生成，暂缓高级维护功能”。
- **当前环节**：T01 已提交；T02 45 项 RED 已运行，等待任务解析及澄清测试覆盖审查，尚未进入 T03 实现。

## 目标 Goal

让用户在 DataMind 中完成 Yang 的核心流程：获得本体、描述任务、回答必要澄清、查看和下载行为树。
默认界面只显示当前版本，不要求用户理解固定版本/编辑底本/工作头，不把治理表单放在生成前面。
沿用现有构建、RDF/OWL、编译验证和持久化能力，补齐自然语言任务入口与 UI 联动。

### 实际使用路径

| 步骤 | 用户看到什么 | 用户主要操作 |
|---|---|---|
| 1. 准备本体 | 现有成对 XML 构建入口，构建模板沿用；也可选择已有本体或导入符合契约的 OWL | 构建；完成后点击“生成行为树” |
| 2. 描述任务 | 当前本体名称、一个当前版本号、任务输入框、模型可用状态 | 输入自然语言任务，点击“生成行为树” |
| 3. 补充选择 | 本次需要明确的零件或目标，候选使用易读名称 | 回答或点击候选；不填写通用知识表单 |
| 4. 查看结果 | 简单的行为树层级预览、结论、XML 下载；参数来源/验证报告折叠 | 查看、下载，或开始新任务 |

本体缺少映射或参数时，显示具体缺口和“下载本体 / 导入修改后的本体”。
本轮仅提供只读行为树预览，不新增三个图画布、通用知识编辑器或 XML 在线编辑器。
基础页只保留一个简短的离线用途说明，不重复展示三组技术状态。

## 代码盘点与实现边界

| 已有能力或 Yang 做法 | 最小接入方式 |
|---|---|
| Yang ontology/README.md：CLI 构建、Protégé 维护；agent/web/index.html：对话与结果两区 | 在现有 DataMind 页面衔接步骤，移开默认高级维护入口 |
| Yang llm_client.py：角色消息、JSON 模式、六字段校验、一次格式修复 | 本地迁移适配器；配置来自 srv_engine，不能依赖 Yang 目录运行 |
| Yang trusted_agent.py：只认可用户明确提及、否定/撤销、换目标失效 | 迁移确定性澄清逻辑；候选保留唯一身份，模型猜测不能成为选择 |
| domains/robotics/generation.py：严格六字段任务生成、参数/模板/符号校验 | 复用；聊天层不能绕过它或直接把模型 XML 当产物 |
| AssetStore.load_robotics / generate_from_version：要求已发布版本 | 保留原契约；增加经过同等结构复验的应用快照入口，候选可试用但不自动发布 |
| RunStore：已有版本快照、事件、manifest 和产物持久化 | 新应用关联实际运行；复用持久化原语保存任务消息，不引入通用会话框架 |
| IR-019：已完成治理后端及复杂 UI | 兼容保留，不作为基础版操作前置；本轮不扩展多人协作能力 |

## 交付 Deliverables

- [x] DR-066、本文与 Yang 源码复核记录；旧方案优先级变更说明。
- [ ] 任务解析适配器及澄清逻辑：DataMind 模型配置、六字段提取、明确选择和多轮修正。
- [ ] 本体应用服务及 HTTP：固定快照加载、会话保存、严格生成、OWL 导入、运行/产物读取。
- [ ] 基础 UI：构建完成直达任务、一个当前版本、对话澄清、只读树预览、下载与明确错误提示。
- [ ] 显式独立演示入口；真实本体缺口不因切换页面或回复“确认”而消失。
- [ ] 可直接运行的离线演示 CLI、真实后端浏览器验收、人工真实模型冒烟说明及统一检查接线。
- [ ] 分批 RED/GREEN、验收映射、旧 UI 契约替代清单、最终审计与全量回归。

## 任务 Tasks（每项一次提交）

| 编号 | 可独立提交的工作 | 完成条件 |
|---|---|---|
| T01 | 复核 Yang 与 DataMind，记录简化方向、接口差距、范围和验收 | 本文与 DR-066 落地；产品方向已由用户选定 |
| T02 | 编写模型配置/响应、六字段和用户明确选择的 RED 测试，映射验收，提交覆盖审查 | 测试可收集，失败来自缺失功能，不是访问真实模型或读取用户数据 |
| T03 | 实现任务解析与澄清，使 T02 转绿并回归 | 已审测试不改；后续 HTTP/UI 接入可直接复用 |
| T04 | 编写应用服务、导入、会话、生成/下载及完整浏览器流程 RED 测试，审查旧 UI 入口契约替代 | 覆盖成功、澄清、缺知识、模型失败、切换任务、重载及原数据入口 |
| T05 | 实现应用服务、基础页面、离线演示并完成端到端回归 | T04 转绿；默认页不再要求治理输入；实际浏览器可走通 |
| T06 | 汇总演示、用户操作说明和交付对照，提交整轮验收 | 用户确认基础流程达到目标后标记 delivered |

T02 只验证第一块任务解析/选择能力，不冒充整条链路已覆盖。
T04 必须验证真实构建、存储、编译和下载，只替换外部模型响应；不能 mock 掉整个后端证明联动完成。
已有冻结测试保留；若默认入口的产品要求发生冲突，在 T04 记录具体替代断言并审查，GREEN 阶段不得通过删测试消除失败。

## 验收 Acceptance

- [ ] RMW-01：从成对 XML 构建成功后可直接进入任务页面；只看到一个当前版本，不需署名、理由、工作头或审核操作。
- [ ] RMW-02：复用 DataMind 的模型配置，角色消息与 JSON 契约正确；未配置、鉴权/连接失败、拒答、截断或错误 JSON 均明确报错，不生成 XML。
- [ ] RMW-03：只接受完整六字段任务；未知、缺失、模糊或否定选择会澄清，模型补猜和助手候选不能代替用户选择；修改上游目标不会沿用旧下游选择。
- [ ] RMW-04：自然语言任务经过真实 OWL 解析、确定性编译及符号验证；预览与下载属于同次运行，XML 与冻结预期按结构和公开参数核对。
- [ ] RMW-05：缺映射/参数的真实本体给出可理解的缺口，不伪造完成；显式演示可走通构建到 XML，演示本体与用户真实本体隔离。
- [ ] RMW-06：符合本平台契约的 OWL 可以导入为新版本并用于任务；不支持/损坏文件给出错误；候选试用不自动审核/发布，旧已发布加载契约不放宽。
- [ ] RMW-07：刷新或重启后可回看会话与结果；换本体、模式或新任务不会显示旧结果；失败/澄清状态没有本次 XML 下载。
- [ ] RMW-08：真实浏览器通过简洁路径完成操作；高级治理表单不在默认路径中，旧数据本体入口仍可用；统一检查通过。
- [ ] RMW-09：下述离线命令退出 0，在新目录留下本体、对话记录、XML/缺口报告与验证结果；报告注明模型是测试替身，真实模型另做人工冒烟。

### 可直接运行的离线演示（T05 交付，当前尚未实现）

```bash
.venv/bin/python scripts/demo_robotics_task.py --case generated --output /tmp/datamind-task-generated
.venv/bin/python scripts/demo_robotics_task.py --case clarification --output /tmp/datamind-task-clarification
.venv/bin/python scripts/demo_robotics_task.py --case missing-knowledge --output /tmp/datamind-task-missing
.venv/bin/python scripts/demo_robotics_task.py --case model-error --output /tmp/datamind-task-error
.venv/bin/python scripts/check.py --suite all
```

输出目录须为新目录，重复运行改用新路径，脚本不清理或覆盖已有输出。
四个案例均使用登记输入及脚本化模型响应；成功/澄清案例显式使用独立演示本体。
missing-knowledge 使用未补充知识的真实构建；model-error 不产生候选 XML。
离线测试证明集成和错误处理，不证明某个在线模型的语义准确率。

## T02 固定的 Python 契约

`domains.robotics.task_model.ModelClient(base_url, model, api_key, timeout=30, transport=None)` 提供 `parse(messages, catalog)` 和 `status()`。
`ModelClient.from_datamind(config=..., environ=..., transport=...)` 接收 DataMind 配置快照与环境快照，不另造 BT_AGENT_* 配置入口。
有效环境值优先于 llm 配置，task_models.bt_extract 优先于默认模型；没有覆盖则沿用默认模型。
`transport(url=..., payload=..., headers=..., timeout=...)` 返回 Chat Completions envelope，仅为替换外部网络提供测试边界。
生产默认 transport 必须真的发 HTTP，连接异常、响应限制和不转发凭据的跳转规则在 T04 验证。
`ModelError` 有稳定 `code` 和可向用户展示的信息；`status()` 不泄露密钥。
`validate_task(task)` 校验六字段及长度，支持 pick_place、unsupported 或未明确的 null 操作。
`domains.robotics.task_dialogue.catalog_from_graph(graph)` 只提供业务名称目录，不传位姿、阈值等机器人配置。
目录项为 `kind, id, label, aliases`，id 使用完整 IRI，保留同名对象区别。
`grounded_mentions(task, messages, catalog)` 返回四字段请求与澄清问题，只重放用户明确选择；已解析对象用完整 IRI，未知名称保留供原 grounding 报错。
唯一上级推导仍由已有 grounding 根据实际 RDF 关系完成，不由模型猜测。

审计与本批执行记录见 [IR-020 validation](../../docs/audits/IR-020-minimal-workflow-validation.md)。

## T02 执行记录（2026-11-05）

新增任务模型测试 30 项、明确选择测试 15 项，合计 45 项。
实际运行：45 项预期失败、0 收集错误、0 跳过；失败均因 task_model / task_dialogue 尚未实现。
21 项元测试、两个新测试文件的 ruff 和 mypy 均通过。
IR-019 审计登记的 134 个已审文件与 10 个冻结生产文件摘要不变；未修改生产实现或旧测试。
acceptance.json 已映射 RMW-02/03；其余要求保留为 planned_requirements，不虚报覆盖。
按 SDD 第⑤步“人 review 测试是否覆盖到位”，本批审查通过后进入 T03。
