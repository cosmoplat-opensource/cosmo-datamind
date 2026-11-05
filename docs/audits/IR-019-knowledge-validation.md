# IR-019 · 机器人知识维护、重建合并与发布审计

- 日期：2026-11-05（Asia/Shanghai）
- 依据：DR-059 / DR-060 / DR-061 / DR-063 / DR-064 / DR-065。
- 当前阶段：T01，范围与验收草案；没有本轮实现或 RED/GREEN 结果。
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
