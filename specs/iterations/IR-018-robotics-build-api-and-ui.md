# IR-018 · 第三阶段：机器人本体构建 API 与平台 UI

- **状态 / Status**: in-progress
- **日期 / Date**: 2026-11-04
- **当前环节**：T04 覆盖已确认；T05 实现、70 项 GREEN 与完整九段回归通过，下一任务 T06；UI 留 T06/T07，整轮仍 in-progress。
- **关联 / Refs**: [[DR-059-unified-domain-semantic-graph]]、[[DR-060-robotics-source-recipes-and-reverse-build]]、[[DR-063-local-domain-registration-and-application-ui]]、[[DR-064-persistent-build-runs-and-artifact-manifests]]、[[DR-065-robotics-migration-and-compatibility-validation]]。
- **前置**：[IR-017](IR-017-ontology-assets-and-persistent-runs.md) 于 2026-11-04 经用户确认 delivered；IR-016 业务内核已交付。
- **方案**：[集成 Proposal](../PROPOSAL-yang-datamind-integration.md) 第 6 节步骤 3 与第 6.1 节前后端联动要求。
- **流程依据**：[SDD skill](../../.agents/skills/sdd-workflow/SKILL.md) 第③～⑤步；本轮范围确认不替代各批新测试覆盖审查。

## 目标 Goal

用户在 DataMind 的“本体构建”页选择机器人配方，上传主行为树 XML 和节点模型 XML，提交后查看真实构建进度、失败定位及新生成的本体候选。
用户可在“本体库”查看机器人资产及固定版本、来源、结构兼容报告和运行历史，下载候选 OWL、报告与 manifest；刷新或重启后仍能回查。
前后端在本轮一起交付，浏览器验收连接实际 HTTP 服务、真实存储和现有机器人内核。
产物是待评审的机器人本体候选；本轮不提供任务到行为树 XML 的页面，不将构建成功表示为机器人已可执行。

2026-11-04，用户在了解 Blueprint、后台执行、RunStore 及本轮 UI/可视化边界后回复“已确认，进行下一步”，确认本轮 Goal / Acceptance 与 RBU-01～10，授权进入 T02。
跨阶段 DR 保持 proposed，不因本 IR 开始而整体批准后续编辑、发布、模型及应用阶段。

### 代码盘点与必须补齐的接入点

| 当前代码事实 | 本轮调整方向 |
|---|---|
| `domains/robotics/importer.py:build_graph()` 只支持 ur_pick_place；`source_validation.py` 有 XML/端口/模板检查，部分错误只有 ValueError 文本 | 保持支持范围，增加稳定的问题代码、来源角色和节点/端口位置，不靠匹配异常字符串猜测位置 |
| `publisher.publish_ontology()` 封装协议并验证 OWL；`AssetStore.publish_version()` 登记正式发布状态 | 构建可复用协议封装和消费兼容检查，但入库状态仍为 candidate；人工评审/正式发布入口留下一轮 |
| `RunStore.create_run()` 要求已有 ontology_id/version_id；finish 的 generated 结果要求候选 XML | 增加来源输入型构建运行及 built 结果，区分输入来源和输出版本；不能先编造一个本体版本，也不能放宽原生成契约 |
| `domain_runs.offline_demo.py` 构建并自动发布，只用于隔离演示 | 正式构建服务直接复用业务内核，不通过调用演示脚本实现 API，不隐式应用 demo_supplement |
| 资产与运行共用 schema 2 SQLite；来源和工件受目录/摘要保护 | 有版本迁移地补构建输入及输出引用，验证原资产、已完成运行、事件、清单及读取行为保持完整 |
| `server.py` 已有 Blueprint、应用级 CSRF/JSON guard、25 MiB 默认请求限制；旧上传会写 uploads.db/按文件名存档 | 新增独立机器人 Blueprint 与成对上传契约，复用应用保护，不把 XML 交给 CSV/数据构建逻辑 |
| Gunicorn 固定单 worker、gthread，启用 max_requests 回收；JOBS 为进程内旧任务表 | 新建有界本地构建执行器，独占宿主恢复和任务认领；考虑 worker 替换，不能每次创建存储对象都执行恢复 |
| `ui/index.html` 的 #build 使用原生 JS；#library 读取 /api/ont/forged；#jobs 读取旧 JOBS | 沿用这些入口及样式，新模块承接机器人表单、目录详情和持久化运行展示，保留旧条目和深链 |
| 现有 `tests/frontend/build-browser.py` 会拦截关键构建接口返回夹具 | 继续作为旧 UI 回归；新增机器人浏览器用例不得拦截核心上传/构建/查询/下载 API 返回成功替身 |

详细盘点见 [IR-018 审计记录](../../docs/audits/IR-018-build-ui-validation.md)。

### 用户操作路径与本轮边界

```text
本体构建 → 选择“行为树 XML → 机器人本体”
  → 选择主行为树 XML、节点模型 XML、UR 抓移放 profile
  → 提交 → 排队/构建进度 → 待评审候选或明确的失败报告
  → 本体库中的指定版本详情 → 来源/报告/OWL/manifest
  → 作业中心或历史链接 → 刷新、重启后回查同一运行
```

| 本轮包含 | 后续 IR 承接 |
|---|---|
| 两类 XML 上传、受支持配方/profile、真实构建和候选保存 | 更多机器人/profile、任意 BTCPP 控制结构 |
| 构建运行、阶段事件、有限队列、超时、启动恢复和显式重试 | 分布式调度、多 worker 部署、用户取消和自动续跑 running |
| 构建表单、进度、领域目录、版本详情、来源与问题定位 | 本体映射/参数/证据编辑、重建合并、评审及正式发布 UI |
| OWL 候选、构建报告和 manifest 下载 | LLM 六字段提取、任务澄清、行为树生成 API/UI |
| 字段式候选详情和来源定位 | 完整关系图/行为树/数据流视图；本轮不增加在线 XML 编辑器 |
| 旧条目只读展示及原入口跳转 | 自动迁移全量旧图谱、聊天或 JOBS |

## 交付 Deliverables

- [ ] `domain_runs/`：来源输入型构建运行、输出版本关联、事务完成与 schema 迁移；现有版本输入型生成服务继续可用。
- [ ] `domains/robotics/`：正式构建服务、来源快照校验及可定位质量问题；协议封装与用户发布状态分离。
- [ ] 本地执行器：有界接纳、数据库任务认领、独占宿主、受控子进程计算及超时、显式启动恢复和新运行重试。
- [ ] 机器人 Blueprint、装配及配置：配方能力、成对上传构建、目录/版本/运行/事件查询、重试、登记工件下载。
- [ ] `ui/modules/robotics-build.js`、`robotics-assets.js`、`ui/index.html` 的必要改动：机器人构建、候选详情、历史/作业跳转和旧入口兼容。
- [ ] 服务、迁移、队列、HTTP 与 UI 行为测试；新增连接真实后端的 Playwright 联动用例。
- [ ] `scripts/check.py` 将新增测试和真实浏览器场景纳入统一检查；保留已有检查段、已审测试及覆盖率门槛。
- [ ] 使用指南、隔离启动/浏览器复核命令、审计报告及实际运行 ID、来源摘要、版本引用、下载文件和必要截图。
- [ ] 更新 acceptance 映射、Proposal 和规格索引；未交付能力继续显式标记后续。

### 最小后端契约（本轮范围已确认，T02/T04 细化可执行断言）

**构建输入与版本输入分开。**
已有生成运行仍固定已发布 ontology_id/version_id，保持其字段含义、状态和 candidate 工件约束。
新增 operation=build_ontology 的运行，输入固定两类 source_asset_id/role/sha256/byte_size、recipe、profile、构建组件及规则版本；提交时没有输出版本。
构建输入引用在终态仍不可变；成功结果另存 output_asset_ref={ontology_id,version_id}，不把输出版本回填冒充原输入。
新增 built 业务结果仅用于构建运行，kind=ontology 的候选 OWL 与报告/manifest 配套；生成侧 generated 与候选 XML 的约束不变。
构建失败/超时没有成功输出版本或 OWL 候选，必须有可追溯问题报告；原始来源可保留供修复和重试。
成功提交协调新资产/版本、运行终态、事件和工件索引，避免出现“目录有成功候选但运行失败”或相反的部分提交。
数据库迁移保留旧 schema 2 的来源、版本、历史和原 manifest；未知 schema 拒绝迁移，失败回滚，不重新生成旧清单。

**文件与质量契约。**
提交必须恰有 behavior_tree 和 node_models 两种角色，UR profile 显式可见，不根据文件名猜角色。
每份文件默认上限 10 MiB，总请求仍受既有默认 25 MiB 上限约束；执行测试时用小的配置上限覆盖超限分支。
文件名仅用于显示，存储使用逻辑 ID 和摘要；不接受客户端服务器路径、任意规则或 demo 补充资料。
接收后按固定来源快照构建，来源改变或损坏时失败，不读取“最新同名文件”。
非法 XML/DTD/实体声明、未知节点、模型冲突、模板/端口不兼容返回 {code,message,source_role,source_asset_id,location}；location 可含 XML 路径、节点/端口或解析行列。
文件级错误可以只定位文件角色；节点/模型冲突须定位相关节点/端口，无法准确定位时明确标记 unknown，不虚构行号。
保留原 ValueError 兼容性，优先新增结构化异常或检查结果，不通过正则解析既有异常文字作为唯一定位机制。
OWL 可包含受支持协议并通过消费侧结构检查，但资产版本 publication_state 仍为 candidate；本轮不调用资产正式发布接口或自动补齐业务证据。
UI 分开显示结构检查、候选发布状态、任务适配未评估和 execution_readiness=not_verified，不使用数据领域 verified/certified 表示机器人能力。

**异步运行与恢复。**
请求完成参数/大小/角色检查及受理后返回 HTTP 202 和 run_id，计算在后台进行，不在上传请求中等待完整构建。
首版一个计算子进程、默认最多四个等待任务；队列满返回 429，不留下声称已受理但永远不会调度的运行。
request_id 作为重发标识；同一 ID 与相同输入返回原 run_id，不重复排队；同一 ID 改变输入返回 409。
排队状态持久化，调度器从数据库认领；接纳容量、运行创建和入队之间不得出现未登记或丢任务窗口。
父宿主持久化阶段事件并提交结果；子进程只做本次快照的计算，不能在父进程已宣布超时后写入成功版本。
默认构建计算子进程预算 300 秒，配置有有限上限；超时终止并回收计算进程，登记失败报告，释放执行名额。
启动恢复由持有存储根独占执行权的宿主执行；另一宿主或 worker 替换不能误中断仍在运行的任务。
遗留 running 标记 interrupted；本轮 queued 构建可重新认领；不自动执行其他 operation 的排队记录，不续跑已中断外部计算。
failed/interrupted 的重试创建新 run 并固定原来源快照，保留 retry_of，不修改原结果。
进度首版使用带 after 游标的事件轮询，刷新不重复追加事件；SSE 可在后续复用同一持久事件源。

**目录与工件。**
统一列表区分持久资产条目与旧图谱只读引用；后者显示“旧数据图谱/未登记版本”，保留 legacy_key 和旧跳转，不能伪造 ontology_id/version_id。
已登记数据资产按 AssetStore 展示其固定版本；同一旧 key 已有对应登记时按明确来源关联去重，不把当前旧图谱自动等同于历史快照。
机器人条目只进入机器人详情，禁止进入 SQL 问数或默认 demo 数据兜底。
响应只暴露逻辑 ID、摘要、大小、状态及可展示名称，不返回部署绝对路径、数据库路径或内部命令。
下载必须同时校验 run_id/artifact_id、受控路径和摘要；未知或跨运行标识返回 404，已登记文件缺失/损坏返回明确的完整性错误，不修改原历史终态。

### HTTP 接口（T05 已实现，页面接入由 T06/T07 承接）

| 接口 | 契约摘要 |
|---|---|
| `GET /api/robotics/capabilities` | enabled、配方、支持的 profile/输入角色和限制；关闭时可读能力状态，不启动执行器 |
| `POST /api/robotics/builds` | multipart：behavior_tree、node_models、name、recipe、profile、request_id；受理返回 202/run_id，错误角色/配置 400，超限 413，重发冲突 409，队列满 429 |
| `GET /api/ontology-assets` | 领域/来源分类、持久资产版本摘要及旧数据图谱引用；只读，不隐式迁移 |
| `GET /api/ontology-assets/<id>/versions/<version>` | 固定版本元信息、来源、构建运行及报告/工件引用；交叉资产版本拒绝 |
| `GET /api/domain-runs?operation=build_ontology` | 有界分页的构建历史；供页面恢复和作业中心展示 |
| `GET /api/domain-runs/<id>` | 生命周期、业务结果、固定输入及输出版本引用、阶段和问题 |
| `GET /api/domain-runs/<id>/events?after=N` | 按序号增量读取持久化事件，不因读取触发恢复 |
| `POST /api/domain-runs/<id>/retry` | 仅支持本轮失败/中断构建，使用新的 request_id，新建关联运行 |
| `GET /api/domain-runs/<id>/artifacts/<artifact_id>` | 候选 OWL、构建报告和 manifest 的登记下载；正确媒体类型与附件文件名 |

应用级 CSRF/JSON guard 继续覆盖新 Blueprint；前端转义所有文件名、资产名和问题文字。
不更改现有 /api/build/*、/api/graphs、/api/jobs 返回形状；旧能力通过原入口保持。
新增 DATAMIND_ROBOTICS_ENABLED 开关（默认关闭），启用后使用显式 DATAMIND_DOMAIN_STORAGE_ROOT，未指定时定位到隔离可配置 WORK 下的 domain_assets 子目录。
关闭功能时新写接口返回 feature_disabled，不启动任务或迁移新库，原数据构建/图谱/聊天保持可用。
配置名称和路径约束由 T04 测试契约固定，T05 已实现；见下方记录及 API 使用指南。

### UI 调整与真实联动

| 位置 | 本轮用户操作和界面状态 |
|---|---|
| 本体构建 #build | 配方切换，默认保留原数据构建；机器人配方显示两个有独立标签的文件槽位、profile/支持范围、提交与清晰的错误提示 |
| 构建进度 | 排队、构建计算、保存、终态；首版计算包含解析/抽取/协议检查，阶段来自后端事件，不伪造百分比；提交等待期间防重复点击，HTTP 错误恢复按钮状态 |
| 本体库 #library | 领域和来源分类；旧数据条目继续走原详情，机器人条目展示候选状态和固定版本，不显示问数按钮 |
| 候选详情 | 来源角色/摘要、版本、节点/技能数量、结构报告和未评审提示；问题可定位到来源/节点；提供本体、报告和 manifest 下载 |
| 作业中心 #jobs | 原 JOBS 与新构建历史分组显示，明确各自 ID 和状态；可返回指定构建或候选 |
| 刷新和重开 | 拟支持 #build?recipe=robotics&run=<id>、#library?asset=<id>&version=<version>；保留原 #graph= / #obj= / 普通页面深链 |

刷新恢复的是运行及输入元信息，不要求浏览器重新填充上传 File 对象，也不自动重新提交构建。
页面离开停止轮询，后台已受理构建继续；关闭功能后保留旧页面可用，已存历史不删除。
本轮以摘要、列表与字段详情承接质量信息，不把三种图视图或正式评审功能夹带为已经完成。
浏览器测试使用真实本地 HTTP 服务、真实双文件上传与存储，成功断言必须进一步核对数据库引用和下载字节。
至少覆盖一次合法来源构建、一次模型/节点冲突、刷新回查、指定版本下载以及服务重启后的历史读取；不能用 route.fulfill 固定成功响应代替核心链路。
界面单元测试可以模拟响应以检查渲染分支，但必须独立标记，不能计为上述联动证据。

## 任务 Tasks（每项一次提交）

| 编号 | 工作与提交边界 | 依赖 | 对应验收 |
|---|---|---|---|
| T01 | 核对代码，建立本 IR、计划验收映射、API/UI 边界和审计起点 | IR-017 delivered | RBU-01～10 |
| T02 | 来源构建运行、schema 迁移、候选提交、问题定位和生成兼容的失败测试；记录 RED、提交审查 | 本轮 Goal/Acceptance 确认 | RBU-01～04、09 |
| T03 | 实现构建服务和来源型运行，使 T02 转绿；保持已审测试不变 | T02 review | 同上 |
| T04 | HTTP/有界执行器/超时/重复请求/恢复/下载/开关的失败测试；记录 RED、提交审查 | T03 | RBU-01～06、08、09 |
| T05 | 实现 Blueprint、执行器、启动装配、查询/下载与配置，使 T04 转绿 | T04 review | 同上 |
| T06 | UI 行为及真实后端浏览器失败测试；覆盖上传、失败定位、目录/下载、刷新/重启与旧入口 | T05 | RBU-05、07～10 |
| T07 | 实现配方 UI、候选详情和作业/历史导航，使 T06 转绿；保存实际浏览器证据 | T06 review | 同上 |
| T08 | 统一环境全量回归、真实页面人工复核路径、审计与使用说明，提交整轮交付人审 | T07 | RBU-01～10 |

各批实现均按 skill 跑必要回归；最终交付必须通过完整检查与真实浏览器联动，不能以 T03/T05 后端通过宣称本轮 UI 功能完成。
T01 阶段测试文件名只是计划，放在 acceptance.json 的 planned_requirements；实际测试创建后再填写 requirements 的真实函数引用。

## 验收 Acceptance（本轮已确认，待实现与交付验证）

- [ ] **RBU-01 来源与配方**：从页面明确提交两种 XML 角色及受支持 profile，固定来源字节/摘要；缺文件、角色/配置错误、超限和重复请求按约定处理，不覆盖同名旧来源。
- [ ] **RBU-02 构建与候选**：实际调用新构建内核和兼容校验，产生保真 OWL 候选及输出版本引用；候选保持待评审，不自动发布或补模拟证据，执行状态未验证。
- [ ] **RBU-03 运行与原子性**：尚无本体版本时可创建来源输入型运行；有界受理、阶段事件、超时、重启与重试行为可验证；失败或事务故障不留下可见成功版本/半份清单，原生成契约不被放宽。
- [ ] **RBU-04 问题定位**：非法 XML、未知节点、模型冲突和不兼容模板/端口显示稳定代码及真实来源/节点位置；页面与后端报告一致，不泄漏部署路径或伪造定位/成功。
- [ ] **RBU-05 目录与版本**：平台展示持久资产与旧数据引用并明确其差别，机器人详情固定资产/版本/来源；刷新或新版本出现不串用旧记录，机器人不进入 SQL 问数或默认 demo 兜底。
- [ ] **RBU-06 下载与完整性**：按运行/工件 ID 下载的 OWL、报告与 manifest 字节和摘要一致；跨运行、越界、缺失及损坏请求明确失败且不改历史；构建输入和输出版本能互相追溯。
- [ ] **RBU-07 页面交互**：真实上传、配方切换、提交/排队/运行/失败/完成状态与后端一致；文件/问题显示安全、键盘可操作，1440/768/390 宽度无主要操作不可见或页面横向溢出。
- [ ] **RBU-08 历史与恢复**：运行及指定版本可通过页面深链重开；事件按游标无重复，刷新不重提任务，真实服务重启后仍可查看；遗留 running 明确中断、queued 构建按契约恢复，新 worker 不误中断活跃宿主。
- [ ] **RBU-09 兼容与隔离**：旧 schema 2 可无损升级且失败回滚，未知 schema 拒绝；既有生成/演示、数据构建、图谱、聊天和 JOBS 无回归；关闭机器人功能不破坏旧能力，校验使用隔离目录且门槛不降低。
- [ ] **RBU-10 可复核联动**：自动浏览器连接真实服务，走通双文件上传到候选详情及下载，并覆盖实际失败、刷新和重启；保存运行/版本/来源/下载摘要及截图，交付启动/复核命令和人工路径，全量检查通过。

### 验收执行与交付证据

沿用 `.venv/bin/python scripts/check.py --suite all` 作为最终统一检查入口；实现后将机器人浏览器用例纳入其中，必要的 Node/浏览器环境写入指南。
新浏览器程序计划位于 `tests/frontend/robotics-build-browser.py`，与已有 mock 型 build-browser 检查独立运行。
测试启动的服务必须启用机器人模块并使用临时 WORK/存储根；输入使用仓库冻结的 XML 和受控负向变体，不读取用户业务目录。
本阶段不调用 LLM，成功来源不应用 demo_supplement；真实缺业务证据可以形成待评审本体，但不能被显示成任务已适配。
T08 交付 `docs/robotics-build-ui.md` 的实际可运行命令和操作路径；这些新增入口目前尚未实现，不能宣称已有浏览器验收通过。
IR-017 的通过结果作为旧能力基线，不替代本轮的 RED/GREEN 与 UI 联动证据。

### T01 记录

- 2026-11-04：用户确认 IR-017 并授权下一步；本次只建立本轮具体范围、任务、最小契约与验收草案。
- 已识别“现有运行强制输入版本”“候选与正式发布分离”“旧页面只读引用”“已有浏览器夹具不能替代真实联动”等接入差异，并纳入上述任务。
- 尚未修改生产代码、UI、已审测试或启动线上构建；尚无本轮测试通过/失败统计。
- 本轮 Goal / Acceptance 确认后开始 T02 的首批失败测试，再按 skill 第⑤步提交新测试覆盖审查。


### T02 范围确认与失败测试

2026-11-04：用户确认本轮范围；新增三组测试及测试辅助模块、schema 2 冻结夹具。
T02 当时尚未实现新增业务代码、HTTP 和 UI；以下是该批固定的 T03 服务边界，最终统计及覆盖表见 [审计记录](../../docs/audits/IR-018-build-ui-validation.md)。

| 接口 / 字段 | T02 固定的含义 |
|---|---|
| RunStore.create_build_run(name, recipe, profile_id, source_asset_ids, session_ref, components) | 从已登记的两类来源创建 queued 运行；本批不负责 HTTP 文件大小、请求幂等或执行器容量 |
| build_input | 固定 name、recipe、profile_id 及 sources 的 source_asset_id/role/sha256/byte_size；不含服务器路径 |
| ontology_id / version_id / ontology_snapshot | 构建运行保持 None；保留旧版本输入型运行的原字段含义 |
| output_asset_ref | 初始/失败为 None，成功记录新候选的 ontology_id/version_id；不回填为输入版本 |
| domain_runs.robotics_build.compute_build(root, run_id) | 对已 running 的构建读取来源快照、实际执行内核及消费兼容验证；只返回 result 与 ontology 字节或 None，不持久提交状态、事件或工件 |
| RunStore.finish_build(run_id, result, ontology) | 核对来源、输出摘要/兼容及结果一致性，原子保存候选资产/版本、报告、manifest、终态及事件；失败为报告，无输出版本 |
| completed / built | 沿用既有生命周期 completed，构建成功业务结果为 built；不增加名为 succeeded 的存储状态 |
| execution_readiness / task_readiness | 构建结果分别为 not_verified / not_assessed；结构通过不表示任务证据齐全 |
| 结构化来源异常 | importer 保留 ValueError 可捕获语义，增加 issues；构建边界补登记来源 ID，位置使用真实行列或 tree_id/node_tag/step_index/model_id/port；无位置则显式 unknown |

本批 stable code：invalid_xml、forbidden_xml_declaration、unknown_node、model_conflict、invalid_port、unsupported_template、source_integrity、build_error。
模型冲突需报告两侧声明；未知节点/端口的测试明确给出实际所在树、节点与端口；不以字符串正则反推结构化位置。
存储故障注入包括第二次文件写入、工件回读摘要检查以及成功行/工件已写入事务后、commit 前抛错，必须回滚所有可见成功索引。
尚未涉及的有界接纳、request_id 幂等、运行超时/宿主所有权、HTTP 返回码属于 T04；页面和真实浏览器联动属于 T06，不能以本批测试替代。
按 skill 第⑤步，本批测试覆盖经用户 review 后才进入 T03；T03 不改已审测试来让其通过。

T02 实测：61 项，4 通过、57 预期失败，0 收集错误/跳过；旧能力针对性回归 74 项、元测试 21 项通过，新增文件 ruff/mypy 通过。
未更改生产代码或已审测试，未执行本轮完整回归；待本批覆盖确认后进入 T03。

### T03 开始记录

2026-11-04：用户了解本批测试及数据库迁移原因后回复“已确认，进行下一步”，确认 T02 覆盖并授权实现。
本任务保持已审测试/夹具不变；HTTP、执行器和 UI 仍分别由 T04/T05、T06/T07 承接。


### T03 完成记录

来源输入型运行、只读构建计算、候选原子提交、结构化来源问题及 schema 2→3 迁移已实现。
已审 T02 的 61 项全部通过；113 个既有测试/夹具文件保持不变。
完整九段检查退出 0：1088 单元通过、5 可选上游跳过，覆盖率 85.0%（门槛 81%）；551 HTTP 通过、8 可选上游跳过，严格 10/10；旧 UI 64 项、UI 实操 50 项（3 未配置真实模型跳过）、旧浏览器 10 项通过。
详细证据见 [T03 GREEN 报告](../../docs/audits/IR-018-t03-green-results.json) 与 [审计记录](../../docs/audits/IR-018-build-ui-validation.md)。
本批没有新增 HTTP/UI，不将旧浏览器回归当作新机器人链路验收；RBU-01～04/09 标为 partial-green-t03，整轮 Deliverables / Acceptance 仍待后续任务及最终交付确认。
下一任务 T04 为 HTTP/执行器/恢复/下载等失败测试，按 skill 第⑤步写完后再进行新覆盖审查。


### T04 · HTTP 与执行器失败测试（待覆盖审查）

2026-11-04：用户回复“继续下一步”，授权在 T03 完成后编写本批失败测试。
本次只增加测试与规格/审计，不实现新 HTTP、执行器或 UI，不修改 T02 已审测试、生产代码或检查门槛。
新增 `tests/unit/test_domain_build_executor.py`（25 项）、`tests/unit/test_robotics_build_http.py`（45 项）与辅助模块 `tests/build_runtime_support.py`。
70 项均因待实现模块缺失而失败，0 收集错误、0 跳过；这表示 RED 已建立，不表示内部行为断言已验证通过。

#### 本批供审查的具体约定

| 边界 | T04 可执行测试约定 |
|---|---|
| `domain_runs.build_service.BuildService(root, max_pending=4, file_limit_bytes=10485760)` | `submit(name, recipe, profile_id, request_id, sources)` 原子受理；sources 按两个角色传 filename/content；返回持久 queued 运行。`retry(run_id, request_id=...)` 新建固定来源的关联运行。 |
| `BuildRequestError` | 带 code/status_code；重复相同请求返回原运行，输入变化为 request_conflict/409；等待队列满为 queue_full/429。幂等登记跨进程保存，重发优先于容量检查，重试 ID 不能挪用于另一运行。 |
| 受理事务 | 请求登记、双来源、queued 运行和初始事件共同提交；并发不能超额或重复登记；事务失败没有已登记的半份来源/运行，允许无索引孤立文件但不作为成功证据。 |
| 组件记录 | compiler_version 记录实际 importer.py 的 SHA-256（不是整个仓库版本），rule_digest 使用实际校验规则摘要，profile 固定，model.invoked=false。 |
| `domain_runs.executor.BuildExecutor(root, timeout_seconds=300, poll_interval=..., compute_fn=None)` | start/stop/is_owner；同一根仅一个宿主和一个计算子进程，其他根独立运行；备用宿主自动竞争释放后的所有权。compute_fn 仅供可信 Python 注入，不能由 HTTP 或环境配置指定。 |
| 计算与提交 | 子进程只计算，父宿主持久提交；首版成功阶段事件为 computing、committing。子进程内部未上报的解析/抽取子阶段不伪装成实时进度，不制造百分比。 |
| 故障与回收 | 超时、进程崩溃、异常、非法结果、提交故障分别报告 build_timeout / worker_crashed / worker_error / invalid_build_result / storage_error；回收子进程、释放名额，不留下成功输出。排队来源损坏报告 source_integrity，继续调度后续任务。 |
| 恢复与重试 | 必须先获得独占所有权才恢复；只恢复本轮 build_ontology，其他 operation 的 running/queued 保持；遗留 running→interrupted，queued 可认领，已终态不重跑；重试保留原输入/组件和 retry_of，不改原历史。 |
| `bp_robotics.init_app(app, workdir=..., legacy_catalog=..., start_executor=True)` | server.py 注入旧目录读取器，蓝图不能反向导入 server；返回 runtime(service/executor/close)，登记到 app.extensions['robotics']；默认启动后台执行器。测试可用 Python 参数关闭执行器以隔离 HTTP 断言，生产配置不暴露该测试选项。 |
| HTTP 受理和查询 | 上表端点保持；拒绝重复/未知字段和额外文件，文件名不参与路径解析；受理 202，格式 400，大小 413，冲突 409，容量 429，存储故障 503/storage_unavailable。生命周期错误在运行报告返回，非法 XML 可以先受理再由后台报错。 |
| 列表和详情 | 列表包在 items；历史 limit 1～100、offset≥0，事件 after≥0；run 详情含可下载工件描述；固定版本详情含 build_run_id；不暴露内部路径/命令。 |
| 旧目录与下载 | 条目区分 entry_kind=asset/legacy_reference；旧 key 仅在明确登记关联时去重，GET 不注册旧资产；跨运行/未知标识 404，缺失/篡改/符号链接 409/artifact_integrity，原历史不改。 |

配置由应用 config 优先、环境变量补充；开关 `DATAMIND_ROBOTICS_ENABLED` 默认关闭，关闭时能力可读，新写接口 503/feature_disabled，不创建新库或启动执行器。
`DATAMIND_DOMAIN_STORAGE_ROOT` 为显式绝对路径，未指定时使用 WORK/domain_assets。
`DATAMIND_ROBOTICS_FILE_LIMIT_BYTES` 默认 10 MiB，必须为正数；总请求继续受应用默认 25 MiB 限制。
`DATAMIND_ROBOTICS_MAX_PENDING` 默认 4，范围 1～64，仅计 queued，运行名额另计 1。
`DATAMIND_ROBOTICS_TIMEOUT_SECONDS` 默认 300 秒，须为有限正数且不超过 3600 秒；非法启用配置在写库前报错。
测试将预算缩短至 30 秒，超时场景使用受控阻塞子进程及 1.5 秒预算，避免依赖 RDF 性能；注入仅存在于测试 Python 组合，不是用户可选算法。

本批包含真实 Flask 应用装配、实际构建子进程、实际宿主终止及新进程导入 WSGI 回查；尚不包含网络服务器/浏览器操作，不能替代 T06 的真实浏览器联动。
详细覆盖、运行统计与文件摘要见 [审计记录](../../docs/audits/IR-018-build-ui-validation.md) 及 [T04 RED 记录](../../docs/audits/IR-018-t04-red-results.json)。
按照 skill 第⑤步“人 review 测试是否覆盖到位”，本批覆盖确认后才进入 T05；届时保持这些测试不变，只修改实现使其转绿。

T04 检查补记：T03 已审 61 项回归通过、规格元测试 21 项通过，新三文件 ruff/mypy 通过；登记的 167 份基线文件（含 113 份既有测试/夹具）摘要未变，未运行本批全量回归。

### T05 开始记录

2026-11-04：用户听取 T04 测试解释后回复“继续下一步”，确认本批覆盖并授权实现。
本任务保持全部已审测试/夹具不变，实现 T04 的 HTTP/执行器契约并完成回归；UI 留 T06/T07，整轮不提前标记 delivered。


### T05 完成记录

已实现受理去重/容量事务、独占宿主与受控计算子进程、恢复重试、机器人 Blueprint、实际 server 装配、目录/版本/历史/事件/下载及功能配置。
T04 已审 70 项全部通过；116 份已跟踪测试/夹具摘要保持不变，验收 criterion 和测试引用未改。
完整九段检查退出 0：1158 单元通过、5 可选上游跳过；覆盖率 85.0%（保留 81% 门槛）；551 HTTP 通过、8 可选上游跳过；严格 10/10；旧 UI 64、UI 实操 50（3 未配置真实模型跳过）、旧浏览器 10 项通过。
单元段实际耗时 806.106 秒，保留全部检查与门槛，将原 720 秒预算调整为有界 1200 秒；其他段仍为 360 秒。
额外完成真实旧 schema 3 库的升级/回滚/只读验证，以及实际 Gunicorn 网络上传、工件摘要核对和整服务重启回查；临时存储和服务均已清理。
配置、隔离启动及可直接使用的 curl 命令见 [API 指南](../../docs/robotics-build-api.md)；结果见 [T05 GREEN 记录](../../docs/audits/IR-018-t05-green-results.json)。
本任务没有新 UI，实际网络验证也不冒充浏览器联动；整轮仍 in-progress，交付与验收复选框留最终人审。
下一任务 T06 编写 UI 行为及真实后端浏览器失败测试，再按 skill 第⑤步提交覆盖审查。
