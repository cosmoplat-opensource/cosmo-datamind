# 机器人建模核心测试接口 v1 — DR-070 / IR-024 T04

- 日期：2026-11-10。
- 状态：2026-11-10 用户了解测试内容后要求继续，T04 测试已人审；T05 已按本接口实现核心，12 个完整回归阶段通过。
- 上层接口沿用 [双路径构建契约](robotics-dual-build-v1.md)，本文件不新增 HTTP 或模型配置入口。

## 分层与范围

`domains.robotics.modeling` 是包内核心，不依赖外部工程路径、Flask、数据库、历史 sessions 或外部工程环境变量。
TBox 使用传入的 T03 BuildRoute 协议；测试只替换该请求边界。
ABox 不接收模型、提示或客户端对象，只读取确认骨架、XML 证据和包内版本化映射。
本阶段的状态转换是纯函数，调用失败不修改输入状态；设计会话持久化、并发修订检查、事务提交与重启恢复仍属于 T06/T07。
HTTP 在 T03 保持 model_build_pending，直到后续服务集成，不因核心测试就提前放开。

## evidence 与 vocabulary

`evidence.ingest_sources(sources)` 接受 `behavior_tree` / `node_models` 到 bytes 的映射。
复用共同 XML 安全与角色限制，按固定角色顺序产生 `{documents, evidence}`，不依赖 dict 插入顺序。
每份 document 有 `id, role, sha256`；evidence 沿用来源工程的 `id, kind, facts, locations` 格式。
每个 location 有 `document_id, path`；相同 facts 可以合并，但各次节点出现的位置必须全部保留。
事实正文、注释或处理指令属于不可信来源数据，不能据此确认阶段、添加术语或修改执行权限。

`vocabulary.load_vocabulary()` 只加载迁入包内的版本化词表，返回含 `sha256, classes, object_properties, data_properties` 的独立快照。
首版词表原始字节 SHA-256 固定为来源清单的 `eef9314fb13144a35efd659f6fd863dcc89cebf1ae0e54c3777bcdcf8e2a06aa`。
本轮不增加在线词表编辑；后续合法升级必须更新来源记录并重新确认，不悄悄改变会话的词表身份。
属性组保留原定义域、值域、数据类型和 scope；多成员的单一并集与多个定义域声明不可混同。

## TBox 检查与纯状态转换

`tbox.validate_result(result, source_refs, message_refs, terminology)` 与 `validate_properties(result, source_refs, message_refs, terminology, classes, questions)` 沿用来源工程的结构化提议字段。
返回校验后的独立副本；非法结构、标识、引用、循环、控制字符和词表外术语抛 ValueError 兼容异常。
类别与属性名称按受控词表标准化；能力问题必须引用用户消息；未覆盖能力问题增加待明确项。
对象属性方向和完整并集、数据类型不可由模型更改；`class_ids` 仅表示属性关联建议，不能自动填入空 `domain_ids`。
scope_review 术语不能进入已选骨架；词表外概念可以留在澄清文本中，但不能成为类别或属性。

纯状态接口：

| 接口 | 行为 |
|---|---|
| `new_design(evidence, vocabulary, namespace=...)` | 返回 classes 阶段、revision=0 的状态，确认字段为空 |
| `revise(state, text, route, session_id=..., timeout=...)` | 选定阶段提议、校验并返回 revision+1 的新状态；不原地修改 |
| `confirm_classes(state, snapshot_sha256=...)` | 确认非空有效类别及能力问题，固定词表／来源并进入 properties；不请求模型 |
| `edit_classes(state)` | 返回 classes，清除当前确认和下游属性；旧输入快照不变 |
| `confirm_schema(state, snapshot_sha256=...)` | 仅 properties 的有效快照可确认，进入 abox 并产生确认封装 |

状态包含 `stage, revision, result, snapshot_sha256, vocabulary, class_confirmation, schema_confirmation`，并携带来源及消息上下文。
空状态仍有 snapshot_sha256 字段，但不能确认。
有效 snapshot_sha256 为当前 result 的确定性 SHA-256；过期确认或状态与确认基线不一致时拒绝操作。
class_confirmation 至少包含独立的 `classes, competency_questions, vocabulary_sha256` 及固定来源身份。
properties 阶段必须验证类别、能力问题和词表未偏离确认基线，再发出模型请求；不能只信任 stage 字符串。
新消息“确认”“直接生成实例”等文字不会调用确认转换。
成功提议不接受模型提供的实例、阶段或确认字段。

一次 revise 最多发出两次请求：一次正常请求，加至多一次 JSON 格式／结构修正。
词表、来源、层次、方向等语义错误不触发再次模型请求；传输失败也不在核心内自动重试。
失败保留调用者原状态；显式再次调用 revise 可重试，不产生重复修订。
两个请求沿用同一 session_id、route 与 timeout；不自行读取密钥或切换运行时。
该 timeout 是每次请求参数；总后台任务预算由后续服务层负责。

## 确认封装与 ABox

schema_confirmation 形如：

```text
{
  tbox: {namespace, classes, object_properties, data_properties},
  sha256: SHA256(canonical JSON of tbox),
  source_sha256: {behavior_tree: digest, node_models: digest},
  vocabulary_sha256: digest
}
```

canonical JSON 使用 UTF-8、ensure_ascii=False、sort_keys=True、separators=(',', ':')。
摘要用于一致性校验，不是授权签名；封装只能由可信服务的确认操作传入，不能把客户端自行构造的封装当作已获批准。
测试可直接构造封装隔离抽取核心，真正 HTTP 端的授权与不可绕过性在 T06 验证。

`abox.extract(confirmation, evidence_bundle)` 校验确认、骨架摘要及两份来源身份，返回 `{result, report}`。
result 沿用来源工程的 `individuals, object_assertions, data_assertions, clarifications, reply`。
report 沿用 `method=xml_rules, source_coverage, missing_terms, skipped_assertions, skipped_instances, omitted_information, selected_unmapped_terms, property_coverage, mapping_sha256` 等字段。
真实 UR 样例必须保留 25 次调用、129 个参数绑定及其具体身份／作用域；不能按技能名合并调用。
缺少映射术语时报告遗漏；未映射属性不补造事实，不将部分覆盖表示为完整抽取。
不支持的控制结构、未知节点、端口冲突、动态输出或递归调用抛 ValueError 兼容异常，`.issues` 包含来源角色及位置／source_ref，不能返回假候选。
`abox.validate_result(result, tbox, evidence_rows)` 检查实例类型、属性域／值域、来源和字面量；bool 不能冒充 integer，字符串也不能冒充 integer。

## RDF 导出

`export.tbox_owl(result_or_schema, namespace)` 与 `export.abox_owl(result, tbox, evidence_rows)` 返回 RDF/XML bytes。
比较以解析后的 RDF 图同构为准，不要求空白节点标识或序列化空白逐字稳定。
每个并集域／值域仅有一个声明，通过 owl:unionOf 列表表达全部成员。
class_ids 不导出为 domain，不生成类级属性值或额外 restriction；实际实例值按 TBox 数据类型导出。
ABox 实例 IRI 使用 `namespace + 'individual/' + id`，保留已选 schema 和全部有效实例断言。
来源先与传入 evidence_rows 核对；禁止从结果自己的 source_refs 反推“允许来源”而自证合法。
来源关联保存在权威 JSON；本阶段不擅自增加 RDF provenance 词表。
不生成 owl:imports，不补造旧 UR artifactContract；可解析的设计 RDF 仍应被旧 UR 消费器拒绝。
设计候选保存资格、应用目录过滤和 API 准入由 T06/T07 继续验证。

## 测试材料

使用仓库已有两份真实 UR XML；两者摘要与来源工程样例一致。
新增 `tests/fixtures/robotics/modeling/ur_selected_tbox.json` 仅提取来源样例的 namespace 和三组已选 schema。
不复制历史 chat_id、用户消息、会话目录、旧证据、模型配置或密钥；来源及裁剪方式记录在同目录 provenance.json。
小词表和模型提议由测试显式合成；它们只证明程序边界，不证明真实模型的建模质量。
