# 机器人知识维护服务（IR-019 T03）

依据 DR-059 / DR-060 / DR-061 / DR-064 / DR-065。
本页说明已实现的本地 Python 服务；新增 HTTP 和 UI 由 IR-019 后续任务接入。
当前 UI 的“构建模板”仍用于双 XML 导入，业务映射维护尚未增加界面入口。

## 存储与版本

`GovernanceService(root)` 的 root 是已有本体资产存储根目录，必须是绝对路径，与 AssetStore/RunStore 共用 SQLite 和登记文件。
可写服务初始化会把 schema 4 事务升级为 schema 5，追加治理表，不转换旧数据 IR 或机器人 OWL。
`read_only=True` 不创建目录、不升级 schema；旧 schema 必须先由可写存储完成迁移。
代码回退到只支持 schema 4 的程序时，不应把 schema 5 数据库交给旧程序写入；回退数据应使用升级前备份。
本次自动验证只使用隔离临时目录，没有对用户的 `workdir/domain_assets` 执行升级或编辑。

## 维护已有候选的调用顺序

下面是 API 示例，需要替换为实际已登记的根目录、资产 ID 和固定版本 ID；不是本轮尚未交付的独立演示 CLI。
原版本可由 AssetStore 的目录/版本读取接口查询，也可来自已完成构建运行的 output_asset_ref。

```python
from pathlib import Path
from ontology_assets import AssetStore
from ontology_assets.governance import GovernanceService

root = Path('/absolute/path/to/domain_assets')
assets = AssetStore(root)
service = GovernanceService(root)
oid, vid = 'ont_实际资产ID', 'ver_实际版本ID'
base = assets.get_version(oid, vid)
head = service.head(oid)

saved = service.save(
    oid,
    base_version_id=vid,
    base_payload_sha256=base['payload_sha256'],
    expected_head_version_id=head['version_id'],
    expected_revision=head['revision'],
    request_id='一次独立操作的唯一ID',
    actor='编辑者署名',
    reason='关联已登记的感知配置，依据为工程记录',
    operations=[{
        'op': 'set_relation',
        'iri': 'https://example.org/ur-skill#PartType_A',
        'relation': 'hasPerceptionProfile',
        'targets': ['https://example.org/ur-skill#Perception_Current'],
        'basis': '工程记录编号及适用范围',
    }],
)
```

保存返回新的 candidate，parent_version_id 指向选择的基线；旧版本字节不变。
工作头已经变化时返回 `GovernanceError(code='head_conflict')`；应保留输入并比较，不自动重发覆盖。
请求重试使用同一 request_id 和同一输入，返回原结果；同 ID 改输入返回 request_conflict。
首次工作头的 revision 为 0；只有实际保存新版本才登记并递增工作头。

允许的业务编辑由 `domains.robotics.authoring` 中的类型、字段和关系白名单定义。
`inspect_knowledge(graph)` 返回从 RDF 读取的实体/关系、来源依据、缺口和编辑 schema；投影不能作为另一份独立可编辑真相源。
未知参数或单位允许留空；错误维数、NaN、错误引用和无依据操作拒绝。
技能、调用、绑定、协议、来源摘要和固定执行结构不开放修改。

## 登记、确认与发布各做什么

- `register_evidence`：登记范围、引用/附件及期限，返回含待审证据的新候选。
- `review_evidence`：审核具体证据，检查当前来源/模型/配置和附件摘要，返回新候选。
- `review_knowledge`：只确认明确选择且已有维护依据的业务对象，返回新候选；不能代替适配证据。
- `review`：对精确 version_id/payload_sha256 给出 accepted/rejected，保存署名、理由和当时报告摘要。
- `publish`：核对工作头与 review_id，重读 OWL 和来源，复验受信结构规则及报告，事务登记发布状态和版本工件。

前三者使用与 save 相同的基线/摘要/工作头/修订/请求 ID/署名/理由参数，分别增加 evidence、evidence_iri/decision、entity_iris/decision；不传 operations。
`review` 使用 version_id、payload_sha256、decision、actor、reason、request_id。
`publish` 使用 version_id、payload_sha256、review_id、expected_head_version_id、expected_revision、request_id、actor、reason。

证据附件先用 `AssetStore.put_source(content, role='evidence', media_type=...)` 登记，证据只引用 source_asset_ids。
服务记录附件摘要/大小/类型并核对字节，不接受服务器路径，也不会自动请求引用中的 URL。
人工署名属于声明式追溯；文件登记、知识确认、审核发布都不是实机技术验证。
结构合格但知识不完整的版本可在明确审核后发布，具体任务缺证据时仍返回 needs_information。

## 来源重建与合并

先使用原双 XML 构建流程生成新来源候选 R，再明确指定 B（可证明的原来源）、L（当前人工版本）和 R。
每份引用包含 ontology_id/version_id/payload_sha256；预览另带当前头和修订。
`preview_merge` 不写版本，返回 token 和冲突；`commit_merge` 固定 token/inputs/decisions 及署名/理由/请求 ID，创建属于目标资产的新候选。
字段冲突支持 current/incoming/manual；非法手工值拒绝。
调用身份变化不猜测匹配，证据范围变化必须作废并重新登记/审核，不能强制保留有效。
新来源必须能核对成功构建运行、来源快照和原构建 OWL；后续重建可追溯此前显式合并的跨资产来源。

## 下载与生成准入

`artifacts(oid, vid)` 返回当前版本独立的 OWL、验证/知识报告和 manifest 登记。
`read_artifact(oid, vid, artifact_id)` 校验资产/版本与摘要，不从旧 build_run_id 下载旧 OWL 冒充编辑后的版本。
`history(oid)` 返回追加的维护与审核记录。

`revoke_evidence` 按资产、版本、证据 IRI 追加 revoked/superseded 状态，不修改已发布 OWL 或旧运行工件。
已有 `domain_runs.robotics.generate_from_version` 对新策略版本读取撤销记录，并在提交生成结果的事务内再次复核，防止计算期间提交的撤销被遗漏。
新运行结果保存 evidence_check（策略、检查时间、撤销记录 ID）；原运行输入和旧 manifest 格式不变。
导出的 OWL 是快照，脱离平台不能获知之后追加的撤销；旧契约继续按旧规则消费，不偷偷要求旧图不存在的新字段。
所有生成结果的 execution_readiness 仍为 not_verified。

事务失败可能留下无登记的不可变文件，但不会通过目录/下载索引暴露为成功结果；本阶段不自动删除未知文件。
独立 `scripts/demo_robotics_review.py` 和新维护 UI 仍按 T06 先写失败测试、T07 实现。
