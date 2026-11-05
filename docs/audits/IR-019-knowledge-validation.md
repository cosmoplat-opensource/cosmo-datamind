# IR-019 · 机器人知识维护、重建合并与发布审计

- 日期：2026-11-05（Asia/Shanghai）
- 依据：DR-059 / DR-060 / DR-061 / DR-063 / DR-064 / DR-065。
- 当前阶段：T03 GREEN；T04 HTTP RED 已完成，83 项中 7 项对照通过、76 项预期失败，待覆盖审查。
- 规约：[IR-019](../../specs/iterations/IR-019-robotics-knowledge-review-and-publication.md)。

## 起点与授权

IR-018 已交付，代码起点为 235b37b；用户随后完成 SSH 推送问题诊断后回复“可以继续下一步了”，本次按工程集成的下一阶段规划处理。
本次只建立可审查的 Goal、Deliverables、Tasks 和 Acceptance，不将“继续”解释为对新具体边界的逐项批准。
既有 Proposal 步骤 4 包含知识编辑、参数/证据、版本差异、重建冲突和发布；本轮拟将这些前后端能力共同交付。
数据库记录级跨域关联不作为前置，完整关系/行为树/数据流图视图与任务应用仍留后续 IR，范围需用户确认。

## 代码盘点

| 代码位置 | 已有行为 | 需要补齐的事实 |
|---|---|---|
| ontology_assets/store.py::_add_version / add_version | 不可变版本文件、父版本、来源和 metadata | 没有工作头 CAS、编辑操作历史和请求幂等 |
| ontology_assets/store.py::publish_version | 消费验证后更新发布元信息；不改载荷 | 没有审核人/理由/版本差异或人工审核前置；直接暴露为 UI 发布会绕过治理 |
| ontology_assets/payloads.py::validate_payload | 候选只做 RDF/XML 可解析与 DTD/实体声明检查 | 编辑操作需白名单和引用约束；不应复用发布校验来禁止缺知识草稿 |
| ontology_assets/storage.py | 当前 schema 4，来源/版本/运行共库，事务与摘要读写 | 治理头、审计、版本工件与证据状态需迁移且不能重写旧 manifest |
| domains/robotics/grounding.py::resolve_request | 业务实体→Profile→参数、确认状态和适配证据检查 | 编辑器必须写同一结构，不能另造知识 JSON 或只改显示节点 |
| domains/robotics/grounding.py::compatibility_confirmed | 主 XML 摘要、实现/零件/槽位、依据字符串、期限 | 尚无节点模型/参数配置摘要、审核来源及平台撤销读取；新治理策略必须补齐并明确旧契约边界 |
| domains/robotics/semantics.py::_configuration_bindings / configuration_paths | 原接口字段为字符串，从 RDF 路径恢复参数/模板 | 保存参数需匹配已支持 UR 接口；不能改变端口定义或把未知单位强行补成已知 |
| domains/robotics/graph.py::invocation | tree_id + index 的调用标识 | 改步骤后不能据旧索引自动认定为同一实体，重建须检查身份/前置条件 |
| domains/robotics/consumer.py::validate_graph | 内嵌受信规则、结构/数据流检查，排除未解决参数错误 | 结构发布和任务证据是独立维度，不应统一用“就绪”覆盖 |
| domains/robotics/demo.py | 明确 demo-only、模拟状态、复制图、不改执行模板 | 只能作显式演示/测试参照，不可搬作真实证据自动补全实现 |
| domain_runs/robotics.py::generate_from_version | 固定已发布版本，实际调用严格内核，保存不可变结果 | 平台治理后的撤销/失效检查需接入，旧报告保持原状；离线快照不能感知未来撤销 |
| bp_robotics.py::version | 从 metadata.build_run_id 获取原运行 result 和 artifacts | 编辑后需要本版本工件，不能继续把原构建结果作为当前版本报告/下载 |
| ui/modules/robotics-assets.js | 固定版本展示、候选状态、来源和下载 | 新维护/评审/差异/冲突界面及版本工件来源接线 |

本次盘点使用本仓库代码与已登记迁移契约，不读取 workdir/domain_assets 中用户新上传的业务知识，也不修改它。

## 拟验证的关键断点

1. 同一版本只有 RDF 权威数据：未修改的实例语义、列表、空白节点、语言/类型不因编辑投影而丢失。
2. 双浏览器编辑及响应丢失重发：不覆盖工作头、不丢草稿、不制造重复版本/发布。
3. 保存不完整知识与发布结构合格版本：业务缺口明确，严格任务生成仍应阻断，执行状态始终未验证。
4. 审核不可“借用”：绑定精确版本、实际摘要和依据范围；编辑/合并改变语义后旧审核不授权新版本。
5. 证据时效及模型/配置变化：新治理版本不得只凭原主 XML 摘要继承适配；撤销追加记录、旧结果不改。
6. 重建三方比较：人工修改、源变化、删除/重命名/步骤移动冲突可见，禁止自动套用旧调用序号。
7. 新版本工件：必须下载新版本字节；跨版本工件、损坏文件和错误旧构建引用不能显示成功。
8. 真后端浏览器：必须经过真实上传/版本保存/审核/发布/下载，完成刷新、重启和旧界面回归。

人工署名属于声明式审计，当前不具备已认证角色权限；证据上传和人工评审也不意味着自动验证物理真实性。
自动化中的证据、署名及批准为合成夹具，验证协议分支；真实缺证据的负向场景与模拟成功分开保存。

## 验收映射与执行顺序

RKG-01～12 目前仅在 acceptance.json 的 planned_requirements 中，没有虚构测试函数。
预计 T02 测试文件：test_robotics_knowledge、test_robotics_evidence、test_robotics_merge、test_asset_governance、test_domain_governance_migration。
预计 T04：test_robotics_review_http；T06：test_robotics_review_ui、test_robotics_review_demo_cli 与 frontend/robotics-review-browser.py。
以上名称是计划，实际编写后再映射真实 test_* 函数；文件名及数量在覆盖审查中固定。
T02、T04、T06 依次先 RED → 用户 review 覆盖 → 对应实现 GREEN，不能改已审测试消除失败。
T08 汇总新界面完整链路的实测证据后再交付人审；旧 IR-018 的 GREEN 不算作本轮功能已经通过。

## T01 验证

仅执行规格元测试、既有验收项/测试引用不变检查与 git diff --check；不运行无生产变更的全量回归。
本次没有新功能通过统计，正式结果会随 RED/GREEN 批次追加。
当前待用户确认具体 Goal/Acceptance，之后进入 T02。

T01 实测：21 项规格元测试通过，0 失败/错误/跳过；既有 requirements 原文与测试引用逐项相同，git diff --check 通过。

## 术语调整与 T02 授权（2026-11-05）

用户要求界面“机器人 profile”改为“构建模板”，并同步 IR、继续下一步；据此确认本轮范围并进入 T02。
仅改用户可见术语，profile/profile_id、ur_pick_place、RDF 类及 API 字段保持不变。
用户明确授权的两处已审测试定位文本同步：tests/robotics_browser_support.py、tests/frontend/robotics-build-browser.py；测试逻辑与业务断言不变。
界面逻辑/规约 61 项通过，真实浏览器 14 项通过；两批均 0 失败、0 错误、0 跳过。
运行证据：`.check-results/ir019-terminology/ui-meta.xml`、`browser/junit.xml`；新治理功能尚未实现。

## T02 · 领域与治理存储 RED（2026-11-05）

本批以术语调整提交 `3c2e0cd` 为起点；用户已确认本轮 Goal/Acceptance 并授权继续。
新建五份测试、一个辅助模块和一份真实 schema 4 冻结快照；未编写治理生产实现。

### 覆盖审查清单

| 测试文件 | 项数 | 主要验证内容 |
|---|---:|---|
| `tests/unit/test_robotics_knowledge.py` | 33 | RDF 身份、语言/类型/空白节点/列表保留；同名不同 IRI；业务映射落在真实 RDF；参数与依据；缺值可保存；错误类型/悬空引用/被引用删除拒绝；保护技能、调用、来源摘要、模式、策略与确认状态 |
| `tests/unit/test_robotics_evidence.py` | 30 | 先登记后审核；实际来源与模型/配置摘要；审核内容不可偷换；范围、签名、期限、退回、撤销/替代；不相关名称变化不误作配置失效；新策略不能退回旧 confirmed 逻辑；真实负向仍未验证执行 |
| `tests/unit/test_robotics_merge.py` | 15 | 语义差异忽略序列化/BNode 标号；保留类型/语言变化；三方独立修改保留；同字段冲突显式决定；删除/步骤身份冲突；证据不能强行恢复；真实构建 B/R、只读预览、提交来源与父版本、过期/伪造/未解决冲突拒绝 |
| `tests/unit/test_asset_governance.py` | 32 | 不可变候选、工作头、摘要、两写者竞争、同请求竞争/重试；失败回滚；固定版本审核与发布；当前版本工件及损坏/串版拒绝；结构发布后缺证据仍不能生成；单独知识确认；证据附件与版本化审核；撤销后新生成阻断而历史产物不改 |
| `tests/unit/test_domain_governance_migration.py` | 12 | 冻结 schema 4 完整性；新迁移逐值保留旧行/字节/manifest、排队及运行状态；迁移失败回滚、并发打开、只读/未知 schema、新进程回查；旧 demo 成功/real 缺信息及实际夹具前提 |

### 实测结果

- 收集 **122 项**：**5 通过、117 失败、0 收集错误、0 跳过**；退出码 **1**，耗时约 **18.57 秒**。
- 112 项失败停在缺失的新治理模块导入；另外 5 项证明当前 schema 4 尚无本轮升级与只读旧版本拒绝行为。
- 缺失模块在测试函数体内导入，因此是已收集测试失败，不是模块收集错误；没有 skip/xfail、空实现或伪造成功。
- 5 项通过分别是冻结快照一致性、两个旧未知 schema 拒绝案例、旧 demo/real 生成结果和实际 XML/RDF 夹具前提；它们不是新治理功能通过。
- RED 阶段多数后续业务断言尚未执行到；T03 必须让完整断言真实执行并转绿，不能把“已写断言”解释为“行为已经实现”。
- 独立前提核验：修改实际源 XML 阈值为 0.73 后，原构建服务能成功构建且 RDF 取值确为 0.73；删除实际调用 stepIndex 后，既有可信 consumer 拒绝其结构。
- 术语变更后的 61 项 UI/规约、14 项真实浏览器已通过；T02 期间原有 **120 个已跟踪测试/夹具文件逐字节未变**，生产文件未改。

执行命令：

```bash
.venv/bin/python -m pytest \
  tests/unit/test_robotics_knowledge.py \
  tests/unit/test_robotics_evidence.py \
  tests/unit/test_robotics_merge.py \
  tests/unit/test_asset_governance.py \
  tests/unit/test_domain_governance_migration.py \
  -v --tb=short --junitxml=.check-results/ir019-t02-red/junit.xml
```

日志 `.check-results/ir019-t02-red/pytest.log`，夹具前提记录 `premises.json`；逐项结果及文件摘要保存在 [T02 机器审计](IR-019-t02-red-results.json)。
冻结快照从实际 schema 4 代码及现有 schema2 历史生成，包含 8 个表、8 次运行、22 个文件；恢复不用当前存储初始化器。
新增测试和原有测试/夹具摘要均已登记；测试覆盖确认后，T03 不得通过改本批断言来转绿。

### 映射和本次边界

RKG-01～09、11 已迁入 acceptance.json 的真实 tests 映射，状态为 partial-red-t02-awaiting-coverage-review；业务 criterion 保持本轮已确认文本。
其中 RKG-09 仅验证存储工件，RKG-11 仅有本批兼容控制与真实负向；HTTP、完整旧功能回归、UI 和端到端验收仍未完成。
RKG-10/12 保留计划项，未填写不存在的函数；T04 编写 HTTP/附件传输/保护测试，T06 编写 UI、CLI 和真实浏览器测试。
本批没有宣称证据内容真实性、署名身份或机器人现场适配经自动验证；演示正例仍明确为 demo，真实缺证据仍是负例。
当前是预期 RED，因此没有执行或声称全量检查通过；全量回归属于实现后的 T03。

按 [SDD skill](../../.agents/skills/sdd-workflow/SKILL.md) 第⑤步“**人 review 测试是否覆盖到位**”，当前提交测试覆盖审查；收到确认后进入 T03，不提前实现新治理功能。

T02 静态与规约核验：新增 6 个 Python 文件 ruff、mypy 均通过；21 项规格元测试通过，0 失败/错误/跳过；git diff --check 通过。


## T03 · 领域与治理存储实现（2026-11-05）

用户了解五组测试后回复“继续下一步”，据此确认 T02 覆盖并开始 T03；不修改已审测试。
新增领域编辑、范围证据、语义差异/合并、治理事务服务和 schema 5 追加迁移。
消费侧按 evidencePolicy 区分新旧策略；平台生成读取有效撤销记录，历史运行和 OWL 快照保留。
初次定向核验 122 通过、0 失败/错误/跳过，JUnit 耗时 214.257 秒；日志位于 `.check-results/ir019-t03-green/targeted.log`。
之后完善源构建工件核对、跨资产再次重建的显式来源链、审核对象/决定的历史记录和输入白名单，并纳入最终回归。
全量命令使用仓库现有隔离工作目录检查器，不复制用户新增本体或修改本地业务存储：

```bash
env PATH="/home/dyh/.local/opt/node-v22.20.0-linux-x64/bin:$PATH" \
  .venv/bin/python scripts/check.py --suite all --report-dir .check-results/ir019-t03-all
```

首次全量仍采用原检查段、81% 覆盖率门槛和 1200 秒单元预算；最终根据本轮耗时调整预算，详见下文。
第一轮 lint/types 全量检查通过；下文汇总最终全量及最后修改补验结果，首轮定向结果保留用于追溯。
新增 API 是本地 Python 服务，尚未装配成新 HTTP/UI 入口；原构建 UI 继续使用 IR-018 路由。

生产实现中的额外保护：真实构建版本需与 run_artifacts 中的原 OWL 摘要一致；后续重建的 B 可由此前显式合并的 incoming 引用追溯，不能随便选同名资产。
写入先验证白名单及并发条件，幂等去重与结果落在同一事务；存储故障不会留下可见的半份版本/已发布记录。
失败事务可能留下未登记的不可变文件，这些文件不能通过版本/下载登记访问；本轮未自动清理未知文件，避免误删历史或用户资料。


T03 最终实现补验：治理存储 32 项、生成准入 2 项均通过，测试文件未修改。
提交边界额外实验使用真实生成内核：先生成临时候选，再于提交前追加撤销；最终状态 needs_information，登记候选数量为 0，结果记录当前撤销 ID。
连续来源重建实验使用三次真实双 XML 构建：两次显式合并后人工零件名称保留，来源阈值更新为 0.74；第二次基线来自上次显式登记的跨资产来源，缺少真实构建依据的基线被拒绝。
这两项均为合成演示/隔离存储实验，不代表现场执行验证；源码和结果随最终机器审计保存。
全量启动后补充的审核历史/输入字段保护、生成提交事务复核，使用原有已审用例作最终补验并补记覆盖率；不会把先前进程已导入的旧模块当作最后修改已验证。

本地服务使用方法见 [机器人知识维护服务](../robotics-knowledge-service.md)。


### T03 最终结果与验证时点

| 检查 | 最终结果 |
|---|---|
| 已审 T02 定向 | 122 通过，0 失败/错误/跳过 |
| 最终治理模块补验 | 32 通过；最终准入补验 2 通过 |
| 清除旧覆盖数据后的最终两模块复验 | 47 通过，0 失败/错误/跳过；另有真实内核提交竞态实验通过 |
| 全量 lint / types | 通过；最后修改后再次执行全仓库静态检查通过 |
| 全量单元 | 1320 通过，5 可选上游组件跳过；1196.827 秒 |
| 全量 HTTP | 551 通过，8 可选上游条件跳过 |
| 严格场景 | 10/10 通过 |
| 原 UI 走查 / 实操 | 64 通过；50 通过 / 3 未配置模型跳过 |
| 原构建浏览器 / 机器人构建真实浏览器 | 10 项通过；14 项通过 |
| 文件冻结 | 127 个已审测试及夹具逐字节不变 |
| 最终覆盖率 | 85.3%，高于 81% 门槛 |


全量命令退出 0，summary.json 十段均为 exit_code=0；没有把条件跳过计作通过。
全量启动后仅继续完善治理历史/输入保护和生成提交复核；这两个文件的旧覆盖率行号/分支数据已明确清除，再用 47 项已审相关测试与竞态实验重测，其他文件沿用全量数据。
首次全量报告覆盖率 84.7%；最后有效覆盖率来自 `.check-results/ir019-t03-green/effective-coverage.json`，不能混淆两次采集时点。
合并批次的覆盖率补采曾以 SIGTERM（143）中断，未计为通过；随后按治理 32 项、合并 15 项拆成独立进程重跑，分别保存 JUnit 和覆盖率数据，再与其余全量数据合并。
定向 coverage 进程对本次未导入的其他模块有 module-not-imported 提示，它们的全量数据保留；最终合并报告退出 0。
最终静态检查、git diff --check 及验收映射元测试通过；没有修改已审测试、断言或冻结输入。

本轮单元耗时接近原 20 分钟上限：1196.827 / 1200 秒，仅剩 3.173 秒。
为覆盖正常环境波动，将 scripts/check.py 的单元预算设为 1500 秒（25 分钟）；其他段仍为 360 秒，测试和 81% 门槛均未削减。
全量实际已经在原更严格预算内通过；改动仅为超时上限与依据注释，CLI/静态检查通过。

最终逐项结果、生产文件与测试摘要、覆盖率清理记录、额外实验完整源码见 [T03 机器审计](IR-019-t03-green-results.json)。
下一步是 T04：为新知识维护 HTTP、附件传输、条件写入、审核发布、版本下载和开关装配先写失败测试，再提交覆盖审查。
IR-019 仍为 in-progress；本批没有新增上述 HTTP 路由、知识维护 UI 或独立演示 CLI。


## T04 · HTTP RED 覆盖审查（2026-11-05）

用户在 T03 后回复“继续下一步”，本批按既定任务顺序编写 HTTP 失败测试，不提前进入 T05 实现。
基线提交：`6d33dac`；分支：`dyh_dev`。
新增 [HTTP 测试](../../tests/unit/test_robotics_review_http.py) 与 [隔离测试辅助](../../tests/robotics_review_http_support.py)，共 34 个测试函数，参数展开后 83 项。
本批固定了 IR 的精确 HTTP 字段、错误码、候选 payload 下载和版本工件投影契约；没有新增生产路由或 UI。

### 本次测试在验证什么

| 分组 | 具体输入与预期结果 | 对应验收 |
|---|---|---|
| 固定版本读取 | 从实际 RDF 读取对象、关系、可编辑范围与知识缺口；返回固定版本摘要和当前工作头；GET 不写记录 | RKG-01/03/08 |
| 保存与并发 | 保存生成新候选，旧 OWL 不变；同请求重放不重复保存；摘要错误、旧修订号、同 ID 改内容被拒绝；两个客户端争同一个工作头只能成功一个 | RKG-02 |
| 业务映射和参数 | 以完整 IRI 将零件类型关联感知参数配置，并保存依据；未知阈值允许保存为缺口；NaN、错误引用、技能结构编辑、无依据编辑被拒绝 | RKG-01/03 |
| 附件上传 | 只登记实际上传的字节和摘要；文件名不能决定存储位置；拒绝空文件、重复文件字段、额外字段、URL、服务端路径及超限内容 | RKG-04/09 |
| 证据与业务对象审核 | 证据登记先是未审核，再经独立审核生成新候选；撤销追加台账并影响当前有效性，旧 OWL 不改；拒绝模拟依据、错误附件角色、缺失附件和错误对象范围；业务对象审核不自动产生适配证据 | RKG-03/04/05 |
| 版本比较与三方合并 | 语义 diff 绑定两端版本；用两次真实双 XML 构建产生来源；同字段冲突必须显式选择，预览不写库，未解决或过期提交失败；没有实际构建血缘的 RDF 副本不能充当来源基线 | RKG-06 |
| 审核与发布 | 审核固定版本/摘要；发布核对审核、工作头和实际字节；缺审核、退回、借用其他版本审核、错摘要、过期头、损坏载荷均拒绝；即使数据库摘要被同步修改，结构不合法仍不能审核通过 | RKG-07 |
| 发布与任务就绪 | HTTP 发布后仍调用真实严格生成内核；缺少业务依据时结果为 needs_information，不保存候选 XML，执行状态仍为 not_verified | RKG-05/11 |
| 当前版本下载 | 新候选 payload_url 返回新 OWL；发布版本提供自己的 OWL、报告、manifest，逐个核对摘要、长度及版本/审核关联；旧构建下载仍返回原字节 | RKG-09/11 |
| 下载完整性 | 错资产/版本、文件缺失、字节损坏、符号链接、登记长度错误均拒绝；不返回下载头或部分文件 | RKG-09 |
| 开关、迁移与错误保护 | 所有 10 个新增 POST 在开关关闭时拒绝，已有版本仍可读；缺库存储 GET 不建库，schema 4 GET 不迁移；应用级 Host/CSRF/JSON/大小限制继续生效；写入故障不留下可见部分记录或泄漏路径 | RKG-02/08/09 |
| 实际应用装配 | 分别在隔离子进程导入 server.app 与 wsgi.application，走真实 HTTP 保存；新进程读取相同版本/历史，并检查原 jobs/graphs/build-defaults 入口 | RKG-08/11 |

所有测试使用临时目录、实际 SQLite/不可变文件和真实 RDF 服务，不接触用户 workdir/domain_assets、不调用 LLM、不启动用户的常驻服务。
合并测试的两份来源通过真实 XML 构建产生，不用伪造完成运行代替；发布和生成断言也调用实际服务。
唯一故障注入是让文件写入抛出 OSError，以验证回滚和错误脱敏；没有 mock 成功响应。
功能关闭测试调用已存在的机器人初始化器，验证新 Blueprint 未来必须随真实应用装配，而不是只在测试里临时注册。

### 实测结果

```bash
.venv/bin/python -m pytest tests/unit/test_robotics_review_http.py --tb=short --junitxml=.check-results/ir019-t04-red/junit.xml
.venv/bin/python -m pytest tests/meta --junitxml=.check-results/ir019-t04-red/meta.xml
.venv/bin/ruff check tests/robotics_review_http_support.py tests/unit/test_robotics_review_http.py
.venv/bin/mypy tests/robotics_review_http_support.py tests/unit/test_robotics_review_http.py
```

- 最终 HTTP 批次：83 项，7 通过、76 失败、0 收集/执行错误、0 跳过；退出码 1，约 78.84 秒。
- 7 项通过是对照：6 项已有 Host/CSRF/JSON/请求大小保护，1 项 T03 实际服务/夹具重新打开检查；不计为新 HTTP 已完成。
- 76 项失败均对应未实现的 HTTP 边界：新增路由尚为 404（包括本应返回 JSON 404 却返回默认 HTML 的情况），或版本详情缺少本版本 artifacts。双客户端和实际应用子进程也在新增路由 404 处失败。
- RED 用例的后续成功流程断言已编写，但现在尚未走到，不能声称这些流程已经验证通过；T05 必须使全部断言实际执行并通过。
- 首轮为 74 项（6 通过、68 失败）；补充映射正例、受信结构复验、严格生成、伪造审核报告、候选下载完整性和夹具对照后得到上述最终 83 项。没有改生产代码来消除失败。
- 验收映射等元测试：21 通过；新增两个 Python 文件 ruff 与 mypy 均通过；git diff --check 通过。
- T03 审计登记的 127 个已审测试/辅助/夹具与 12 个生产文件 SHA-256 均不变；新增文件另行登记摘要。
- 本批仅新增测试与规约/审计，不重复执行全量 GREEN；当前全量会包含这些预期 RED，不能宣称本批全量通过。T03 的十段 GREEN 是上一任务的历史结果。

逐项结果、失败分类、基线冻结摘要及新测试摘要见 [T04 机器审计](IR-019-t04-red-results.json)。
RKG-01～09、11 已追加真实测试函数映射，原 criterion 和原测试引用保持不变；状态仍为部分后端 GREEN。
RKG-10/12 的 UI、浏览器全链路与 CLI 留在 T06 编写失败测试，不提前标为覆盖或交付。

### 当前审查边界与下一步

本次请审查上表是否覆盖预期接口行为，特别是“保存知识、审核业务对象、审核证据、审核版本、发布”这几个动作的区别。
依据 [SDD skill 第⑤步](../../.agents/skills/sdd-workflow/SKILL.md) 的“**人 review 测试是否覆盖到位**”，本批停在覆盖审查；用户确认后进入 T05，实现 HTTP/版本详情并跑全量回归。
这次没有需要访问本机真实机器人或认定现场适配的验收，所有材料均为合成测试输入；UI 操作和实际浏览器交付仍属后续任务。
