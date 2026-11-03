# IR-017 · 存储基础测试与验收记录

## 当前状态

用户在澄清 IR-017 不是最终阶段、后续另含 UI 与联动验收后，授权“继续下一步”。
据此确认 IR-017 本轮范围与 AST-01～09 验收，进入 T02；不将此授权当作尚未编写测试的覆盖确认。
T02 仅新增测试及规约记录，没有创建 ontology_assets 或 domain_runs 的生产实现。
当前为 SDD 第⑤步，等待本批测试覆盖审查。

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

依据 [SDD skill](../../.agents/skills/sdd-workflow/SKILL.md) 第⑤步“人 review 测试是否覆盖到位”，本批待用户确认后才进入 T03，实现时不改已审测试来取得 GREEN。
