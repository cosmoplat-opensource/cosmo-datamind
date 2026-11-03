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
