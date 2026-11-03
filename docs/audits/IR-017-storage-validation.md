# IR-017 · 存储基础测试与验收记录

## 当前状态

用户在澄清 IR-017 不是最终阶段、后续另含 UI 与联动验收后，授权“继续下一步”。
据此确认 IR-017 本轮范围与 AST-01～09 验收，进入 T02；不将此授权当作尚未编写测试的覆盖确认。
用户已明确确认 T02 测试覆盖并授权实现；T03 已实现 ontology_assets，首批 30 项通过，全量回归实际退出 0。
T04 已新增 49 项测试：1 项已有保护通过，48 项预期失败，待本批覆盖审查。
domain_runs 尚未实现，整体 IR-017 仍未交付。

## T02 RED 结果

- 日期：2026-11-03。
- 收集 30 项：0 通过、30 预期失败、0 收集/运行错误、0 跳过；pytest 退出码 1。
- 所有失败均为 `ModuleNotFoundError: No module named 'ontology_assets'`；模块导入放在测试内部，不阻止 pytest 收集和展示用例。
- 三个测试文件和辅助模块的 ruff 检查通过；21 项规约/文档检查通过。
- [机器记录](IR-017-t02-red-results.json)保存命令、各用例失败原因和测试文件摘要。
- 原始日志：`.check-results/ir017-t02-red/pytest.log`；JUnit：同目录 `pytest.xml`。

复测命令（预期退出 1，待 T03 实现后转绿）：

```bash
.venv/bin/python -m pytest tests/unit/test_ontology_asset_catalog.py tests/unit/test_ontology_asset_versions.py tests/unit/test_legacy_asset_adapter.py -v --tb=short
```

## 本批需要审查的行为

| 测试文件 | 展开项数 | 主要断言 | 验收关联 |
|---|---:|---|---|
| `tests/unit/test_ontology_asset_catalog.py` | 7 | 同目录区分数据与机器人；机器人不能从数据 IR 入口加载；空目录和未知 ID 不回退；来源字节/角色/媒体类型/摘要可恢复；未知领域拒绝；新 Python 进程可读取且不导入 server | AST-01、02、08 |
| `tests/unit/test_ontology_asset_versions.py` | 15 | 新版本不改旧字节；父版本不能跨资产；非法载荷、领域类型、未知来源拒绝；新构建 OWL 可登记，发布前不可消费；发布复验真实内容；相同 IRI、语言标签、类型值、空白节点和 RDF 列表隔离保留；新版本不替换旧版加载依据 | AST-02、03 |
| `tests/unit/test_legacy_asset_adapter.py` | 8 | 有效编辑进入快照；重复登记幂等；显式更新建立子版本；来源摘要变化不能被相同载荷掩盖；无效/空来源不补默认数据；调用真实旧图谱读取接口，原文件、聊天和 JOBS 保持不变 | AST-03、08 |

具体例子：

1. 数据本体和机器人本体均能登记，但把机器人版本交给数据 IR 读取入口必须失败，未知 ID 也不能回退到 demo。
2. 一个版本记录“零件”，后续版本改成“已审核零件”；再次读取旧版本仍必须得到“零件”。
3. 已有图谱通过编辑日志增加“装配件、工件”别名，新目录必须登记含别名的有效 IR，而不是仅复制旧文件。
4. 两个资产拥有相同 IRI 但标签不同，分别读取时仍是各自的图，不能合并成一个对象。
5. OWL 上传者声称校验通过，但实际协议、参数绑定或 real/demo 模式不兼容，仍不能发布或加载为可消费版本。
6. 新构建 OWL 发布成功后真实任务仍缺少证据，应继续返回 needs_information，不因“已存储/已发布”而生成候选。

## 测试所约定的接口

以下接口名由本批测试明确，属于 IR 允许细化的 Python 服务边界，不是新增 HTTP 路由。

| 接口 | 输入输出约定 |
|---|---|
| `ontology_assets.AssetStore(root)` | 显式隔离目录；可重新打开，不依赖 server 全局态；操作结果返回独立字典/列表 |
| `create_asset(name=..., domain=...)` | 受支持领域为 data / robotics，返回 ontology_id |
| `put_source(bytes, role=..., media_type=...)` | 保存原始字节，返回 source_asset_id；角色即使字节相同也不能混淆 |
| `get_source / read_source` | 分别读取登记元数据与精确字节 |
| `add_version(ontology_id, bytes, payload_kind=..., parent_version_id=..., source_asset_ids=..., metadata=...)` | 返回新版本；类型与领域一致；验证父版本及来源引用；原字节保持不可变 |
| `list_assets / get_asset / list_versions / get_version / read_payload` | 按登记身份读取，版本必须属于指定资产，未知项不使用默认值 |
| `load_data_ir(ontology_id, version_id)` | 返回数据 IR；机器人载荷明确拒绝 |
| `publish_version / load_robotics` | 固定资产和版本、显式模式；发布复验协议及语义后记录 published 和真实 validation，消费不能绕过发布状态 |
| `ontology_assets.legacy.register_legacy(store, enumerate_graphs=..., load_effective=..., provenance=...)` | 注入旧目录枚举、有效 IR 读取和来源摘要；返回 legacy_key 与资产/版本引用，不写旧存储 |

候选只需是可保存的类型化载荷，不一定已经满足机器人发布契约；保存草稿与发布消费两道边界分开测试。
重复登记同一 legacy_key、有效载荷和来源摘要复用原版本；有效载荷或来源摘要变化时显式登记新版本。
列表排序、SQLite 表名、文件内部布局不作为这些行为测试的实现限制。

## 输入与预期的独立性

数据快照以测试明确给定的对象、别名和字节为预期；RDF 以提交前字节及图同构为预期，不调用新存储模块生成预期结果。
机器人发布正例由已交付的 IR-016 构建器从源 XML 新构建，未直接复用预置成功 OWL。
在不实现存储模块的情况下，额外独立检查了现有夹具前提：新构建 OWL 的真实缺证据拒绝、实际 server 编辑回放、图谱/聊天只读接口和原文件不变，检查退出 0。
该前提检查只证明测试输入合理，不计为新的存储功能通过。
测试均使用 tmp_path 或现有隔离 conftest，不修改用户业务 workdir，不访问网络模型或机器人。

## 未在 T02 完成的覆盖

- AST-02 的固定版本成功生成及完整业务链路由 T06 承接；本批验证旧版精确加载和真实缺证据拒绝。
- AST-04～07 的运行/事件、恢复、并发、事务故障、工件清单及路径/完整性由 T04 承接。
- AST-08 本批仅覆盖旧 IR、图谱读取、聊天及 JOBS 保护；完整问数、HTTP/UI 回归在后续集成和 T08 执行。
- AST-09 的完整跨进程演示由 T06/T07 承接；本批仅跨进程读取资产和数据版本。
- 本次没有执行全量回归；新的预期 RED 测试存在时，不能声称当前工作树全绿。
- 新 UI、页面调用新后端及完整产品验收仍在 Proposal 步骤 3～7，不以本批或旧 UI 回归代替。

依据 [SDD skill](../../.agents/skills/sdd-workflow/SKILL.md) 第⑤步“人 review 测试是否覆盖到位”，本批已由用户明确确认，随后进入 T03；三个已审测试文件和辅助模块摘要保持不变。


## T03 GREEN 与回归记录

日期：2026-11-03。用户已明确确认 T02 覆盖并授权实现。
实现 ontology_assets 的资产、来源、不可变版本、发布/加载和旧图谱适配；服务不依赖 server 导入，旧接口继续独立使用。
来源和版本保留原始字节，写入时独占创建文件并核验摘要，然后提交 SQLite 索引；重新读取时再核验摘要。
候选与发布状态分开；实际 OWL 通过现有消费契约复验后才记录 published，调用方 metadata 中的成功声明不参与授权。
旧目录适配器接收有效 IR 与来源回调，登记快照及编辑摘要，不迁移或覆盖聊天、原 IR 和编辑日志。
共享 RDF 锁移至 srv_context，server 的 SPARQL/SHACL 与资产发布使用同一把锁；锁范围不包含模型调用。

已审的三个测试文件及辅助模块摘要与 T02 机器记录一致，没有调整断言、跳过用例或修改夹具取得 GREEN。
30 项定向测试全部通过；将 ontology_assets 纳入覆盖率统计，保持 81% 门槛。
全量命令：

```bash
env PATH="/home/dyh/.local/opt/node-v22.20.0-linux-x64/bin:$PATH" .venv/bin/python scripts/check.py --suite all --report-dir .check-results/ir017-t03-all
```

命令实际退出 0，无失败段或补验。

| 检查 | 结果 |
|---|---|
| lint / 类型 | 通过 |
| 单元 | 948 通过，5 项可选上游依赖跳过 |
| 覆盖率 | 86.8%，门槛 81% |
| HTTP | 551 通过，8 项可选上游依赖跳过 |
| 变序 | 10/10 通过 |
| UI 走查 | 64 通过 |
| UI 实操 | 50 通过，3 项因未配置真实模型端点跳过 |
| 构建页浏览器 | 10 通过 |

机器结果、已审测试摘要和日志摘要见 [T03 GREEN 记录](IR-017-t03-green-results.json)。
报告目录为 `.check-results/ir017-t03-all/`，模块接口及存储布局见 [README](../../ontology_assets/README.md)。

T03 只完成资产与版本基础；运行、事件、manifest 和恢复尚未实现，后续故障/并发测试尚未审查。
没有新增 UI/API，也未验证真实模型或机器人；上述 UI 检查是旧功能回归，不是新机器人页面联动验收。
IR-017 保持 in-progress；下一任务 T04 编写运行、事件、工件与恢复失败测试，无需再次确认 T02 覆盖。

## T04 RED 与测试覆盖审查

日期：2026-11-03。用户了解资产存储用法后授权继续下一步，本批按原任务安排先写失败测试。
没有修改 T02 已审测试、T03 生产实现或检查门槛，没有编写 domain_runs 实现。

### 结果与复测

收集 49 项，1 通过、48 预期失败、0 收集/运行错误、0 跳过，pytest 退出 1。
通过的是现有 AssetStore 拒绝未知数据库 schema 且不修改原数据；其余均因 domain_runs 缺失而失败。
三个测试文件和辅助模块的 ruff、mypy 检查通过。
机器结果、失败原因与测试摘要见 [T04 RED 记录](IR-017-t04-red-results.json)。

```bash
.venv/bin/python -m pytest tests/unit/test_domain_run_store.py tests/unit/test_domain_artifacts.py tests/unit/test_domain_storage_integrity.py -v --tb=short
```

日志与 JUnit 位于 `.check-results/ir017-t04-red/`。
本次没有运行全量回归；T03 全量通过记录仍保留，但不能据此说加入 RED 后的工作树全绿。

### 覆盖内容

| 文件 | 展开项数 | 检查内容 | 验收关联 |
|---|---:|---|---|
| `test_domain_run_store.py` | 20 | 固定输入/版本/组件；带命名空间的会话引用；空查询及非法输入；聊天文件保护；显式恢复；真实子进程异常退出和重新读取；中断/失败重试；并发事件、游标及终态竞争；非法状态变更拒绝 | AST-04、05、06 |
| `test_domain_artifacts.py` | 22 | 产物字节与 manifest 对照；缺信息等业务拒绝不附候选；错误结果；不一致完成请求；写文件、摘要和事务故障；缺失、篡改及符号链接逃逸；文件名约束、工件 ID 与运行归属 | AST-04、06、07 |
| `test_domain_storage_integrity.py` | 7 | 未知 schema 不重建；运行创建/完成时固定载荷损坏阻断；初始化运行存储保留原资产并共用一个数据库 | AST-07 |

本批测试需要人审的关键规则：

1. 运行保存当时的任务、模式、本体版本、来源摘要和组件版本；出现新版本或调用方修改字典，均不得改变旧记录。
2. 会话只保存 namespace/id 引用，同名但来源不同的会话不混在一起；在明确配置旧聊天目录的新进程中创建运行，不复制或改写聊天正文。
3. 打开存储只读取/初始化，不自动中断活跃任务；宿主显式执行启动恢复，才把遗留 running 标为 interrupted，重复恢复不增加事件。
4. 重试使用原快照创建新 run，保留 retry_of，原记录、事件及产物不被重写。
5. 四个线程各写十条事件，必须全部可查、各运行内序号连续且无重复；after 游标只返回之后的事件。
6. 同一运行同时收到两次完成请求，只允许一次成功提交，另一次明确拒绝，不能重复产生完成事件和可见工件。
7. 完成记录、终态事件、XML/报告索引及 manifest 一起可见；写入或事务故障不能留下部分可见的“成功”。
8. 缺信息、需澄清和不支持是已处理的业务结果，保留报告但不附候选；执行错误记为 failed。
9. manifest 来自固定输入和实际工件，逐项核对 ID、名称、媒体类型与摘要，不包含自身摘要，不接受模型凭据入库。
10. 工件读取只接受登记 ID 并校验所属运行、目录和摘要；文件损坏时读取/清单校验报错，原历史记录不被改写。

### Python 服务接口与状态约定

以下名称为本批测试细化的接口，不是 HTTP 路由；操作返回独立字典/列表，错误返回异常。

| 接口 | 行为约定 |
|---|---|
| `domain_runs.RunStore(root)` | 复用 AssetStore 根目录和 domain.sqlite3，初始化不破坏旧资产，不导入 server |
| `create_run(...)` | 输入 ontology_id、version_id、operation、task、mode、session_ref、components；校验引用和摘要，派生 ontology_snapshot 并保存 queued 记录 |
| `get_run / list_runs(session_ref=...)` | 读取固定记录；session_ref 包含 namespace 和 id；未知 run 不回退或自动创建 |
| `start(run_id)` | queued → running，并在同一事务保存状态事件 |
| `append_event(run_id, kind=..., payload=...) / events(run_id, after=...)` | 运行内单调连续的 seq，从 1 开始；游标不重复返回，终态禁止追加普通过程事件 |
| `finish(run_id, result=..., artifacts=...)` | 仅允许 running 完成；generated 必须有候选，负向结果不能附候选；错误结果对应 failed，其余已处理结果对应 completed |
| `recover_interrupted()` | 由宿主在启动时显式调用，返回被中断的运行 ID 列表；不处理 queued 或已终止记录 |
| `retry(run_id)` | 对失败/中断记录创建新 queued run，沿用原快照并关联 retry_of，不复用原产物 |
| `artifacts / read_artifact(run_id, artifact_id) / manifest(run_id)` | 查询登记产物、读取精确字节与验证清单；清单读取也检查其引用工件的完整性 |

运行记录的 status 是生命周期状态；result.status 是 generated / needs_information / needs_clarification / unsupported / error 等业务结果。
状态事件的 payload.status 对应生命周期状态；调用者的进度事件使用 kind=progress。
artifacts 输入是 name、kind、media_type、content 字节的列表，不接受任意服务器来源路径。
服务自动生成 kind=manifest、name=manifest.json 的工件；该文件不可由调用方覆盖，清单只列其他产物的摘要。
`finish` 失败时，数据库记录、事件和可见索引保持调用前状态；宿主可另行记录失败，或者在启动时恢复为中断。
未登记文件可能遗留，不计为成功也不能通过登记 ID 下载；自动清理不在本批实现范围。
components 包含 profile_id、compiler_version、rule_digest、prompt_version、model；未调用模型时显式 model.invoked=false，凭据字段应拒绝且不回显。

### 测试边界与故障方式

合成任务使用数据 IR 的 inspect 操作和显式 storage-test 组件标识，XML 只是存储用字节，不代表已通过行为树语义校验或真实机器人生成。
预期字节、摘要和参数来源由测试直接给出；manifest 必须与登记快照、下载字节独立比对，不由被测模块生成另一份预期清单。
真实机器人内核、模型、UI 和下载 HTTP 路由均不在本批调用范围；真实 OWL/生成结果接入及 CLI 演示由 T06/T07 承接。
本批检查已存在本体版本的运行记录；更完整的来源构建任务编排仍需后续业务接入测试。
故障注入复用 T03 的 Storage.write_bytes 与 transaction 边界：第二次文件写入报错、写入后破坏字节、真实事务即将提交前抛出数据库异常。
提交前故障用于验证实际 SQLite 回滚，不是模拟断电或磁盘硬件故障。
进程恢复测试启动新 Python 进程，将运行置为 running 后 os._exit(23)，再由另一个新进程读取已完成记录、校验 manifest 并显式恢复中断记录。
所有文件写入、损坏、删除及符号链接操作都限定在测试临时目录，不修改用户工作目录。

依据 [SDD skill](../../.agents/skills/sdd-workflow/SKILL.md) 第⑤步“人 review 测试是否覆盖到位”，本批先提交覆盖审查。用户听取解释后回复“确认没问题”，据此进入 T05；上述 RED 结果保留为历史证据。


## T05 实现与验证

日期：2026-11-03。T04 覆盖已获用户确认，实现代码使 49 项已审测试全部转绿，0 失败、0 错误、0 跳过。
T02/T04 的八个测试与辅助文件 SHA-256 均与各自 RED 审计一致，没有修改已审测试。
新增 domain_runs 的接口校验与 RunStore，复用 ontology_assets.Storage；domain_runs 纳入覆盖率，门槛仍为 81%。
静态检查与类型检查通过；全量命令退出 0，九个检查段全部通过。

### 实现与边界

- SQLite 同库保存运行、按运行递增的事件、工件索引；保存固定任务、模式、组件、本体及来源快照，已有会话只以 namespace/id 关联。
- 状态转换在写事务内完成；显式启动恢复将遗留 running 标记 interrupted，重试创建关联的新 queued run；终态不可覆盖，并发完成只提交一次。
- 文件先独占写入并核对摘要，然后在同一事务提交终态、事件及所有工件索引；写文件、摘要和提交故障保持数据库原状，允许不可见的未登记文件遗留。
- manifest 自动汇总固定输入与结果、参数依据、实际工件摘要；读取时核对清单及引用工件，文件损坏不会改写历史记录。
- 数据库 schema 1 → 2 只新增运行相关表和索引；未知版本仍拒绝打开，不重建原库。

T04 的本体初始化测试验证当前版本的共库与保真，不能单独证明真正的旧 schema 迁移。
为补核这一点，另在临时目录执行一次手工迁移检查：从提交 9f9562d 的 Storage 创建 schema 1 数据库，登记来源、资产和版本，再用新 RunStore 打开。
检查结果：schema 为 2，旧版本记录及来源/载荷字节不变，可以创建新运行并读取初始事件。
这项手工检查单独记录在 `.check-results/ir017-t05-migration.json`，不算入 49 项已审测试数量。

重启恢复必须由单一宿主在接收工作前执行，不是对仍在工作的其他进程进行心跳判断，也不自动续跑外部调用。
存储接口检查产物与业务结果一致性及文件完整性；XML 语义验证仍由调用方通过 IR-016 内核完成。
完整内核接入、跨进程 CLI 演示由 T06/T07 承接；本批没有新 API/UI、后台队列或 SSE，不能据此宣布平台集成完成。
接口用法及限制见 [domain_runs README](../../domain_runs/README.md)。

### 全量回归结果

```bash
env PATH="/home/dyh/.local/opt/node-v22.20.0-linux-x64/bin:$PATH" .venv/bin/python scripts/check.py --suite all --report-dir .check-results/ir017-t05-all
```

| 检查 | 结果 |
|---|---|
| lint / mypy | 通过 |
| 单元测试 | 997 通过，5 项因可选上游引擎未安装跳过 |
| 覆盖率 | 87.0%，门槛保持 81% |
| HTTP | 551 通过，8 项因可选上游引擎未安装跳过 |
| 变序 | 10/10 通过 |
| UI 走查 | 64 通过 |
| UI 实操 | 50 通过，3 项因未配置真实模型端点跳过 |
| 构建页浏览器 | 10 通过 |

机器结果、迁移检查、已审测试摘要及回归日志摘要见 [T05 GREEN 记录](IR-017-t05-green-results.json)。
检查脚本使用隔离临时工作目录，没有改写用户已有聊天、编辑及业务文件。
这些 UI 检查属于旧功能回归，不是新机器人 UI 与后端的联动验收。
T05 完成，下一任务 T06 编写真实内核接入与跨进程演示失败测试；IR-017 保持 in-progress，AST-09 仍为计划项，交付清单不提前勾选。

## T06 RED 与整链路测试覆盖审查

用户在 T05 完成后授权“继续下一步”，按任务顺序编写接入与演示测试。
本批没有修改生产代码、检查门槛或 T02/T04 已审测试；原八个测试/辅助文件 SHA-256 与 T05 记录一致。

### 结果

30 项全部预期失败，0 收集/运行错误、0 跳过，pytest 退出 1。
失败原因是接入模块 `domain_runs.robotics` 或演示入口 `scripts/demo_domain_storage.py` 尚不存在；缺失导入位于测试函数内，不会阻止测试收集。
新增三个文件的 ruff / mypy 检查通过；本批整理后 21 项验收映射及文档元测试通过，git diff --check 通过。
没有运行全量回归；T05 GREEN 是上批历史基线，不能表示新增 RED 后的工作树全绿。

```bash
.venv/bin/python -m pytest tests/unit/test_domain_storage_pipeline.py tests/unit/test_domain_storage_demo_cli.py -v --tb=short
```

原始输出及 JUnit 在 `.check-results/ir017-t06-red/`；测试摘要、各项失败原因和辅助检查见 [T06 RED 机器记录](IR-017-t06-red-results.json)。

### 本批检查哪些事情

| 测试组 | 展开项数 | 核心预期 |
|---|---:|---|
| 真实内核输出与存储 | 1 | 两份 XML 新构建 OWL，demo 补充资料作为来源登记；发布并指定版本加载后实际调用生成内核；保存的候选字节、报告、六项参数依据与内核输出一致，来源及编译器/规则摘要可核对 |
| 新旧版本生成 | 1 | 同一资产发布 threshold=0.61 的新版，旧版生成仍使用 0.72；两个结果都指向各自版本，旧 OWL 字节不变 |
| 真实负向结果 | 5 | 缺证据、缺少实体选择、不支持操作、不支持附加要求、非法任务字段分别保留准确结果与报告，不产生候选；执行错误记为 failed |
| 不可消费版本 | 5 | 未发布、模式不符、数据本体、版本不存在、载荷损坏，在生成和创建运行前拒绝 |
| 内核异常与提交失败 | 2 | 内核意外异常成为有报告的 failed 运行；持久化提交失败必须抛错，不伪装成功，保留 running 供显式恢复 |
| 新进程查询真实产物 | 1 | 实际生成后，用新 Python 进程逐字节核验产物并读取资产、事件、运行和 manifest，与原记录一致 |
| 离线演示全部/单场景 | 4 | 三个场景都可独立执行；全部运行展示双领域目录、指定旧版本生成、真实缺证据无候选、崩溃与恢复；独立核验使用真实子进程 |
| 只读核验 | 1 | 活跃 running 不被中断，运行/事件及磁盘文件保持不变 |
| 重复执行 | 1 | 新建唯一运行目录和存储根，前次数据库、工件及所有来源夹具字节保持不变 |
| 输入异常 | 2 | 来源缺失或非法 XML 必须退出非零并报告 error，不能算成预期的“缺证据”成功 |
| 独立核验发现损坏 | 3 | 候选、manifest 或本体载荷被修改后，核验命令退出非零，历史终态不被改写 |
| 命令退出码 | 2 | 演示返回 passed=false 或抛异常时，CLI 必须退出非零 |
| 核验无效目录 | 2 | 不存在或空目录明确失败，不自动初始化新数据库并把空检查当作成功 |

并发事件、双完成竞争、事务回滚、来源/工件路径约束及旧会话隔离继续由 T04 的已审测试守护，本批不复制这些存储层用例。
用户业务目录保护由隔离的 legacy 哨兵文件核验；测试不读写当前用户业务数据。

### 拟定的接入接口（T07 实现）

`domain_runs.robotics.generate_from_version(root, *, ontology_id, version_id, task, mode, session_ref)` 返回持久化完成后的运行记录。
它接收同一根目录内已经发布的机器人版本，先复用 AssetStore 的加载与消费校验，再创建 operation=generate_bt 的运行，调用现有 generation.generate_task，最后保存结果和工件。
任务是六字段结构化输入；本批不引入自然语言提取、在线编辑或机器人执行。
组件信息由实际执行侧派生：profile 来自固定版本，compiler_version 采用 `sha256:<compiler.py 实际字节摘要>`，rule_digest 来自实际消费校验；未调用模型时 model.invoked=false、prompt_version=null。
此编译器摘要只标识编译器模块，不声称覆盖整个仓库代码或第三方依赖环境。

正常生成必须保存原候选字节，不能仅存摘要或重新拼一份 XML。
持久化 result 与原内核结果相同，但去掉临时 candidate_path；validation 工件保存同一 result JSON。
持久化下载通过运行及 artifact_id 定位，不把生成临时目录的路径写入永久清单。
业务负向结果保留具体 issues，无候选；内核意外异常转换成 error 报告。
持久化失败则向调用方抛异常，不把数据库未提交解释成已经处理完成；残留 running 后续按已审恢复规则处理。

### 拟定的演示与独立核验入口（T07 实现）

```bash
.venv/bin/python scripts/demo_domain_storage.py --case all --output-dir .check-results/domain-storage-demo
.venv/bin/python scripts/demo_domain_storage.py --case demo-success --output-dir .check-results/domain-storage-demo
.venv/bin/python scripts/demo_domain_storage.py --case real-missing-evidence --output-dir .check-results/domain-storage-demo
.venv/bin/python scripts/demo_domain_storage.py --case interrupted-recovery --output-dir .check-results/domain-storage-demo
.venv/bin/python scripts/demo_domain_storage.py --verify-storage /absolute/path/from/summary/storage_root
```

这些命令尚未实现，当前不能作为已可运行的交付物。
三个场景的预期分别是 generated、needs_information、interrupted；双领域目录与旧版本固定包含在成功场景中，因此覆盖 IR 要求的四类结果。
成功场景保存 demo 标记与模拟补充资料来源，并展示旧版 0.72 在新版存在后仍被使用；不把模拟证据当作真实证据。
中断场景由真实子进程把运行置为 running 后以退出码 23 退出，再由另一个新进程显式恢复。
每次演示新建唯一子目录，写 summary.json/summary.md；JSON 至少包含 run_dir、storage_root、producer_pid、passed、cases 和 verification。
每个正常场景记录 case、expected_status、actual_status、passed、ontology_id、version_id、run_id；中断场景另记 crash_pid、crash_returncode、recovery_pid。
verification 包含独立进程 pid、passed、run_ids、assets，必须来自对磁盘记录和实际工件的重新检查。
`--verify-storage` 向 stdout 输出上述核验 JSON，遇到损坏或无效存储退出非零；只读检查不自动执行恢复。
测试通过继承给子进程的 sitecustomize 记录实际 PID，禁止网络连接/DNS、归档工程或预制 OWL 读取以及 server 导入，避免只重建内存对象冒充跨进程检查。

### XML 对照与测试前提

成功用例的字节保真检查使用实际内核输出；另外独立解析原 XML，只修改已约定的六个公开参数位置，再逐树比较标签、属性和子节点顺序。
该预期构造不调用编译器，因此能够发现保存了原始未替换模板或更改固定树结构的情况；不验证机器人现场动作或自然语言理解。
新旧版本用例直接断言 main 流程对应调用位置的 threshold 为 0.72/0.61，不把子树中的黑板引用当作参数值。

在未实现新接口的前提下，另用现有内核做独立前提检查：两份 XML 新构建并发布、demo 生成及六参数 XML 对照、新阈值发布生成、五种真实负向任务的状态均符合预期。
还实际启动四个子进程，验证网络、预制 OWL、归档、server 导入禁用机制及 PID 日志有效。
这些只是夹具和检查机制的前提验证，不计入新功能通过数量，也不替代 T07 后的 30 项真实联动测试。

依据 [SDD skill](../../.agents/skills/sdd-workflow/SKILL.md) 第⑤步“人 review 测试是否覆盖到位”，本批先提交审查；用户听取测试解释后回复“继续下一步”，据此确认 T06 覆盖并进入 T07。上述 RED 记录保留，整份 IR 尚未交付。


## T07 实现与验证

用户听取 T06 覆盖解释后授权继续，已审的 30 项测试在本批实现后全部通过，无失败、错误或跳过。
T02/T04/T06 共 11 个测试与辅助文件摘要保持不变。

### 实现内容

- `domain_runs.robotics.generate_from_version` 指定已发布版本加载，复用 IR-016 严格生成内核；保存实际候选字节、报告、参数依据和组件快照，去掉临时 candidate_path。
- 生成异常保存 failed 报告；无效版本在创建运行前拒绝；持久化提交异常向外传播，不冒充已完成。
- `domain_runs.offline_demo` 与 CLI 从两份 XML 新构建、发布入库，完成三个场景；中断、恢复及最终核验都使用真实子进程。
- `domain_runs.verification.verify_storage` 校验目录、来源、版本、快照、事件和工件；资产/运行存储增加 read_only 模式，使用 SQLite mode=ro，不初始化、不迁移、不恢复、不写文件。
- 独立生成及发布涉及 RDF/SHACL 的步骤复用既有共享锁，没有引入模型调用或服务器导入。
- 完整命令与示例见 [演示说明](../domain-storage-demo.md)，实际执行及全量结果如下。

### 检查预算调整

30 项已审接入/CLI 测试实际耗时 208.617 秒；T05 全量单元段为 259.349 秒。
新增实际 OWL 构建、校验及跨进程演示后，原单元段 360 秒总上限不足以容纳整个套件。
`scripts/check.py` 仅将 unit 段超时设为 720 秒，其他段仍为 360 秒；未删除、跳过或改写已审测试，覆盖率门槛保持 81%。
新 CLI 子进程自身仍受 60 秒超时约束，CLI 测试单次调用受 90 秒约束，超时继续失败。
本次调整是扩大完整测试套件的运行预算，不放宽业务断言或候选生成条件。


### T07 实际演示与全量结果

执行 `.venv/bin/python scripts/demo_domain_storage.py --case all --output-dir .check-results/ir017-t07-demo`，退出 0。
输出根为 `.check-results/ir017-t07-demo/20261103T095756Z-4havdesr/`，保存 summary.json/summary.md 与实际存储、工件。

| 场景 | 实际结果 |
|---|---|
| demo-success | generated，旧版本候选使用 0.72，已存在新版，报告含六项参数依据，execution_readiness=not_verified |
| real-missing-evidence | needs_information，报告实际业务确认/映射/适用性证据缺口，无候选 |
| interrupted-recovery | interrupted，真实子进程异常退出后由另一个新进程恢复，无候选 |

随后另行运行 `--verify-storage` 指向该存储根，退出 0；核验 PID 为 62546，与生产进程不同。
完整运行标识、工件摘要、来源追溯及独立核验 JSON 已保存至 [T07 GREEN 记录](IR-017-t07-green-results.json)。

```bash
env PATH="/home/dyh/.local/opt/node-v22.20.0-linux-x64/bin:$PATH" .venv/bin/python scripts/check.py --suite all --report-dir .check-results/ir017-t07-all
```

全量命令退出 0，九个检查段全部通过：

| 检查 | 结果 |
|---|---|
| lint / mypy | 通过 |
| 单元测试 | 1027 通过、5 项可选上游引擎缺失跳过；耗时 525.403 秒，低于 720 秒预算 |
| 覆盖率 | 84.6%，门槛保持 81% |
| HTTP | 551 通过、8 项可选上游引擎缺失跳过 |
| 变序 | 10/10 通过 |
| UI 走查 | 64 通过 |
| UI 实操 | 50 通过、3 项未配置真实模型端点跳过 |
| 构建页浏览器 | 10 通过 |

沿用现有覆盖率统计口径，CLI 子进程执行不会自动合并到父 pytest 进程的覆盖率；实际子进程验收由对应已审测试和独立演示证据证明。
旧页面回归通过不代表已新增机器人 UI 或完成新前后端联动；本轮实际交付范围仍为存储及离线接入。
T07 实现与检查完成，T08 整理最终交付记录；未将 IR 提前标记 delivered。


## T08 最终交付审查

实现提交：`bc38671`。T08 只做证据、验收映射及文档核对，不修改实现或已审测试。
本任务引用 T07 刚完成的全量回归、三场景演示及独立核验，生产文件摘要与执行证据一致；没有为相同代码重复跑一遍全量套件。
最终元测试为 21 项通过；验收 criterion 与 T06 已审版本逐项相同。

| 验收 | 已完成的检查与证据 |
|---|---|
| AST-01 双领域目录 | 资产目录用例通过；实际演示同库保存 data / robotics，类型化入口隔离 |
| AST-02 不可变与保真 | 来源/OWL 保真、发布准入与版本测试通过；旧版 0.72 与新版 0.61 生成独立对照，旧字节不变 |
| AST-03 身份与旧图谱适配 | 相同 IRI 的资产/版本隔离、旧编辑有效快照、重复登记与来源变化用例通过 |
| AST-04 运行与会话关联 | 固定输入/会话引用/结果持久化；独立进程读取实际生成历史，已有聊天保护用例通过 |
| AST-05 恢复与事件一致性 | 显式恢复、重试、并发事件/完成竞争及游标用例通过；演示真实崩溃和独立恢复，核验不影响活跃运行 |
| AST-06 工件提交与清单 | 实际内核输出字节与报告保存、manifest 关联、写入/摘要/事务失败及提交异常用例通过 |
| AST-07 路径与完整性 | 任意路径/跨运行/符号链接/损坏/schema 保护通过；独立核验可发现候选、清单和本体损坏 |
| AST-08 旧能力保持 | 统一隔离环境全量九段通过；可选上游和未配置模型的跳过如实保留，无新 UI/API 联动声明 |
| AST-09 可直接复核 | 演示三场景实际运行与独立核验退出 0；重复执行、只读、输入错误、退出码和文件完整性测试通过 |

全部自动检查已完成；Deliverables/Acceptance 的勾选与 delivered 状态等待用户最终交付确认。
机器记录见 [T08 最终审查](IR-017-t08-delivery-review.json)，操作说明见 [离线演示](../domain-storage-demo.md)。

审查时可重点查看演示 summary.md 中的三种结果，以及成功场景的 candidate / validation / manifest：
候选应带 DEMO 标记和六项参数依据；真实缺证据结果不应含候选；中断运行应保留 queued → running → interrupted 历史。
独立 `--verify-storage` 命令应退出 0，并且不改写历史或中断活跃运行。

边界保持：这是存储基础和离线接入验收，不涉及 LLM 提取、后台队列、新页面/HTTP 下载、真实机器人执行或新前后端联动。
后续产品集成仍按 Proposal 步骤 3～7 的独立 IR 继续。
依据 [SDD skill](../../.agents/skills/sdd-workflow/SKILL.md) 第⑨步“人确认这一轮做完了”，在用户确认前不把本 IR 标记 delivered。
