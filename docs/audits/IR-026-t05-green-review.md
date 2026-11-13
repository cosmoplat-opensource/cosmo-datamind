# IR-026 T05 集成实现评审 — DR-072

日期：2026-11-12。状态：57 项已审集成用例及全量 13 段通过；IR-026 未交付，T06 未开始。
依据：[适配规约](../../specs/contracts/robotics-application-adaptation-v1.md)、[T04 测试评审](IR-026-t04-red-review.md)、[IR-026](../../specs/iterations/IR-026-robotics-application-adaptation.md)。

## 实现后的操作流程

通用 TBox＋ABox 构建仍直接入库，不选择应用，也不在构建／浏览时执行应用检查。
点击行为树按钮后，后端按固定版本依次检查 TBox 角色、执行结构、当前任务及适配依据。
缺类别或不兼容的角色打开原设计；导航本身不修改类别，也不调用模型。
缺执行字段进入独立适配记录，用户核对／选择角色对应，先确认角色，再明确提交生成应用版本。
缺业务信息返回原任务，复用零件／工位／载具／槽位澄清、感知／放置配置、参数和确认表单。
适配不调用模型；原有建模模型和任务描述解析继续使用已有调用路径。

## 实现边界

- [ApplicationService](../../domain_runs/robotics_application_service.py) 保存独立角色确认、固定输入、工作头和原任务引用；幂等受理、并发、工作头 CAS、失败回滚与显式重试均使用真实 SQLite／RunStore。
- 核心适配被阻塞时，保留原诊断代码、expected／observed 和 source_refs，不合并成通用来源错误；保存失败另记 system.save_failed。
- [领域检查器](../../application_contracts/robotics_checks.py) 提供精确问题及三类受控动作；保留旧 consumer_incompatible 摘要，业务补全依赖有效执行结构。
- 角色建议不授权执行；完整 IRI、类型和语义由服务复核，客户端不能注入 confirmed／ready／executor 等完成凭据。
- [ApplicationAssets](../../ontology_assets/robotics_application.py) 校验总 JSON 当前事实、实际 OWL、版本及运行清单；父版本不就地改写。
- 任何声明了 application_contract 的版本都进入版本化读器；未知版本拒绝，不落回旧读取器绕过 JSON 校验。
- 每次应用版本派生都由 [AssetStore](../../ontology_assets/store.py) 同事务同步总 JSON，涵盖任务和原维护保存路径；原设计快照仅作为历史依据。
- 当前应用版本经完整工件检查后自包含；治理、运行快照及生成不再要求读取原双 XML，其他适配附件和证据／撤销规则继续检查。
- 任务使用原 grounding、编译器、SHACL 和数据流校验器；strict 在业务确认完成后单独核对适配依据，offline 与 real／demo 隔离规则保留。
- [页面接点](../../ui/modules/robotics-application.js) 复用现有卡片和行为树任务布局；刷新／宿主重启只读恢复，完成后查看应用版本或明确切换回原任务。

## 工件与存储

应用完整 JSON 使用 robotics-application-design v2，旧 robotics-design v1 OWL／JSON／manifest 字节保持不变。
单一总 JSON 含当前有效事实、原 TBox／ABox／确认／消息／来源、原模型组件记录、角色与身份映射、覆盖、补全及版本血缘。
适配成功与后续补全都保存同资产的新候选版本；工作头在 CAS 后一起更新，不自动发布或覆盖其他分支。
[增量迁移](../../ontology_assets/application_schema.py) 为 schema 8 增加适配、请求和应用文档表，仅扩展适配运行的输出引用约束；旧字段及工件保留。
GET 不建库或迁移；上线须备份数据库及工件并重启宿主加载新路由，回退使用匹配备份，不伪造 PRAGMA 降级。
本轮只操作临时测试根，没有迁移用户运行库或重启现有服务，没有提交 Git。

## 验证记录

T04 的 57 项已审用例、共用夹具，以及 T02 的 70 项核心用例保持原哈希，不更改断言。
服务／HTTP 合计 41 项通过；新增 Chromium＋Gunicorn 浏览器 5 项通过。
任务链已分别跑通 4 项对象／配置／无原 XML 场景与 7 项 strict／返回／错误／模式场景；全量统一结果也已通过。
先前定向核心／旧宿主合计 89 项通过；lint、320 个文件的类型检查通过。
诊断透传完善后另跑最终服务 21 项与 HTTP 20 项；额外使用真实双静态生产者 XML 完成 v1 构建再适配，验证 execution.binding_ambiguous 及来源引用保留，且无输出或半套版本。
未知工件版本另用实际存储版本的元数据探针验证：完整读器、任务开始和使用合法组件参数的运行快照均拒绝；并补跑 7 项有效 v2／篡改／来源检查。
最终全量 13 段全部通过：单测 2284 passed／5 skipped，覆盖率 86.0%；5 项跳过均为原有可选上游引擎。新增浏览器 5 项与原 17 项全部通过。
完整机器记录见 [T05 结果](IR-026-t05-green-results.json)。不提前勾选业务验收或宣称真实用户本体验收通过。

当前测试新增了真实任务链和重复总 JSON／RDF 验证；T03 旧单测基线已耗时 2017 秒，因此仅把单测进程预算由 2100 秒增至有界的 3600 秒，保留全部测试选择、覆盖率门槛和其他阶段预算。
实际新本体副本、旧 UR 样例和最终使用确认继续交 T06；深度问数、删除和完整详情 UI 分别属于 IR-027～029。
