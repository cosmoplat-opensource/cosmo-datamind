# 机器人知识维护服务（IR-019 T03 / T05）

依据 DR-059 / DR-060 / DR-061 / DR-064 / DR-065。
本页说明本地 Python 服务、T05 HTTP 接口和 T07 知识维护 UI / 离线演示。
“构建模板”用于双 XML 导入；业务映射在本体库固定版本的“维护知识”入口操作。

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
独立 `scripts/demo_robotics_review.py` 和新维护 UI 的用法见本页 T07 操作指南。


## T05：通过 HTTP 调用

已有 `server.app` 和 `wsgi.application` 随机器人模块装配治理 Blueprint，不需要另开一个后端服务。
继续使用现有启动方式与 `DATAMIND_ROBOTICS_ENABLED` 开关；部署新代码后需重启后端进程才能加载新增路由。
本批没有修改用户 `.env`、重启常驻服务或迁移用户实际存储。

先从 `GET /api/ontology-assets?domain=robotics` 选择资产与固定版本，再读取：

```text
GET /api/ontology-assets/<ontology_id>/versions/<version_id>/knowledge
```

响应中的 `knowledge` 是该版本的 RDF 投影，包含 entities、schema、provenance 和 issues。
`head.version_id` / `head.revision` 用于下一次条件写入，`payload_sha256` 用于固定本次编辑底本。
`evidence` 包含证据声明、确认状态、当前有效性及原因，`reviews` 是该版本审核记录，`history` 是资产维护历史。
它们不会把旧版本的 RDF 内容替换为当前工作头内容。

例如保存一项业务映射时，发送以下 JSON；示例中的 ID、摘要和修订号必须取自真实读取结果：

```text
POST /api/ontology-assets/<ontology_id>/drafts
Content-Type: application/json
```

```json
{
  "base_version_id": "ver_从读取结果复制",
  "base_payload_sha256": "从读取结果复制64位摘要",
  "expected_head_version_id": "ver_从head复制",
  "expected_revision": 0,
  "request_id": "本次操作唯一ID",
  "actor": "操作者署名",
  "reason": "根据已核对的工程记录建立业务映射",
  "operations": [{
    "op": "set_relation",
    "iri": "https://example.org/ur-skill#PartType_A",
    "relation": "hasPerceptionProfile",
    "targets": ["https://example.org/ur-skill#Perception_Current"],
    "basis": "工程记录编号和适用范围"
  }]
}
```

201 返回新候选；请求重发保持 request_id 和正文不变。409 时保留当前输入，重新读取 head 并比较版本，不能盲目重试覆盖。
提交对象完整 IRI；不要把展示名称或 IRI 的末尾片段当作对象 ID。

| 操作 | 路由后缀（共同前缀 `/api/ontology-assets/<ontology_id>`） |
|---|---|
| 比较同资产两个版本 | `GET /diff?base=<version_id>&target=<version_id>` |
| 登记附件 | `POST /evidence-sources`，multipart 单个 file，返回来源 ID；没有自动确认 |
| 登记/审核证据 | `POST /evidence`、`POST /evidence-reviews` |
| 审核指定业务对象 | `POST /knowledge-reviews` |
| 来源合并预览/提交 | `POST /merge-previews`、`POST /merges` |
| 审核版本/发布版本 | `POST /versions/<version_id>/reviews`、`POST /versions/<version_id>/publish` |
| 撤销/替代证据 | `POST /evidence-revocations`，正文含完整 evidence_iri |

完整请求字段和错误码见 [IR-019 HTTP 契约](../specs/iterations/IR-019-robotics-knowledge-review-and-publication.md#http-与-ui-接线契约t04-固定)。
JSON 路由严格校验字段和类型；应用级 Host/CSRF/JSON guard 继续按原契约工作。
署名是用户声明的追溯信息，不是认证身份。

版本详情 `GET /api/ontology-assets/<ontology_id>/versions/<version_id>` 中的 `payload_url` 可下载候选自身的 OWL。
未发布治理候选的 artifacts 为空；发布后提供该版本 ontology、validation、manifest 三类工件的 download_url。
沿用服务端返回的 URL，不用 origin_build_run_id 拼出旧文件 URL 代替当前版本。
旧构建运行的下载 URL 保持兼容，仍返回原始构建字节。

开关关闭时所有新增 POST 返回 503，已有版本的 GET 和下载仍可用。
缺少数据库的读取返回 404；不兼容 schema 返回 503，不借 GET 创建或迁移数据库。
附件上传受原单文件限制和应用请求总大小限制；文件名不决定服务器存储路径。
HTTP 操作不会启动行为树执行，也没有增加自然语言生成或 XML 在线编辑接口。

## T07：在页面中维护和评审

使用现有 `./start.sh` 启动后端；`.env` 中 `DATAMIND_ROBOTICS_ENABLED=1` 启用机器人写入。更新代码后重启服务并刷新页面。

1. 在“本体构建”选择机器人构建，上传主行为树 XML 与节点模型 XML，选择“构建模板”，等待构建完成。
2. 在“本体库”打开机器人固定版本，点击“维护知识”。页面显示编辑底本、当前工作头与修订号。
3. 在“知识维护”选择对象和编辑方式，填写修改依据，点击“加入修改”。确认待保存列表后，填写操作者署名和操作理由，点击“保存新候选”。每次保存产生一个新版本。
4. 在“证据管理”上传附件或填写文字引用，明确技能实现、零件类型、可选放置位及带时区有效期。登记后独立接受/退回证据；撤销或标记替代写入追加记录。
5. 在“版本比较”明确选择两个固定版本，逐项查看语义增删改及原值/新值。
6. 需要更新来源时，先回原构建页用新双 XML 构建另一候选，再到原资产的“重建合并”选择 B（原构建）、L（当前人工版本）和 R（新来源版本）。预览后逐项选择，提交新候选。调用身份变化不允许强制按旧步骤序号套用。
7. 点击“机器人评审”进入现有评审页的机器人区域，接受或退回当前版本。接受后才可发布；发布时服务端再次检查版本、报告、字节和结构。
8. 从评审区域或本体库固定版本详情下载当前版本 OWL、报告、manifest。刷新页面或重启服务后，固定版本链接仍可回查。

字段留空可登记为未知，但发布结构规则中的必填字段仍须补全；例如阈值缺失时允许保存草稿，审核会拒绝。结构发布通过仍不代表任务适配通过，执行状态保持“未验证”。
遇 409 并发冲突，页面保留当前底本和未保存操作，不自动覆盖新工作头。离开未保存页面须明确放弃，关闭/刷新由浏览器提示。
多目标关系可在“关联对象”列表中选择多个对象；清空关联是显式解除关系，不会级联删除对象。
原子/复合技能与执行模板只读。本轮提供表单、关系条目、差异和冲突操作；完整图画布、BT 生成界面、XML 在线编辑不在本轮范围。

## T07：直接运行离线演示

在项目根目录执行以下命令，使用新建的空目录，无需启动后端、配置模型或联网：

```bash
REVIEW_DEMO_DIR="$(mktemp -d /tmp/datamind-review-demo.XXXXXX)"
.venv/bin/python scripts/demo_robotics_review.py --case all --output-dir "$REVIEW_DEMO_DIR"
.venv/bin/python scripts/demo_robotics_review.py --verify "$REVIEW_DEMO_DIR"
```

`--case` 也可分别选择 `incomplete-publication`（缺依据仍阻断任务）、`concurrent-edit`（旧工作头冲突）、`rebuild-conflict`（来源合并并使旧证据失效）、`tamper-detection`（独立副本损坏被拒绝）。
输出包含 `summary.md`、`summary.json`、`storage/`；损坏场景额外包含故意损坏的 `tamper-probe/`。主输出保持可核验，不能把损坏探针的非零退出码误解为主演示失败。
核验从数据库枚举固定版本、来源、运行、审核和工件，检查实际字节、摘要和引用；不会初始化、升级、恢复或修复存储。
生产和自动核验使用不同进程。全部材料为合成示例，只验证软件流程，不证明机器人现场能力。
非空输出目录拒绝覆盖；重复演示请重新执行 `mktemp` 得到新的空目录。

浏览器联动验收与统一检查：

```bash
.venv/bin/python tests/frontend/robotics-review-browser.py --output .check-results/robotics-review-browser
.venv/bin/python scripts/check.py --suite all
```

两者使用隔离服务和存储；统一检查包含原功能回归、新状态/CLI 测试及新真实浏览器套件。前端状态测试需要 Node 在 PATH 中。
