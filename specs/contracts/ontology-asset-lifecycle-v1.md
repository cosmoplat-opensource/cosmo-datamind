# 本体资产生命周期 v1 — DR-157 / IR-029 T01

日期：2026-11-16。
用户授权继续 IR-029；沿用 DR-157 的逻辑删除方向，T01／T02 先冻结可审阅的规约与失败测试，尚不启用产品删除入口。

## 状态与边界

逻辑删除单位是 ontology_id，覆盖其全部不可变版本；不是删除对象、关系、单个版本或共享来源。
生命周期 active／deleted 与 publication_state、治理 head、设计 revision、任务 revision 分别存储。
旧资产初始 active、lifecycle_revision=0；每次成功删除／恢复加一，重复受理不重复加一。
恢复使用原 ontology_id、version_id、载荷摘要、来源、发布状态和历史工件，不创建替代本体或重写文件。
恢复后的生命周期修订与删除前不同，旧 ready、缓存和受理依据不能重新变为有效；必须按现有规则复查版本、证据和配置。
第一版不做永久清理、物理擦除、来源删除、版本删除或留存期限自动化。

## 存储和历史

在 domain.sqlite3 的 schema 8 上加法迁移到 9；不改写既有 assets／versions／sources 字段及不可变工件。
生命周期状态、幂等请求、审计事件及旧 key tombstone 使用独立表；未发生生命周期变更的资产可投影为 active／0。
状态变更、修订、幂等结果、审计事件在同一个 BEGIN IMMEDIATE 事务提交，任何失败全部回滚。
请求编号按 ontology_id 作用域唯一；相同 request_id 和相同完整输入重放原操作，不重做变更；输入不同返回 request_conflict。
提交响应分离不可变 operation 和当前 lifecycle；旧删除请求在恢复后重放仍返回原删除 operation，但 lifecycle 必须是当前 active 状态，不再次删除。
审计仅记录成功转移，包含操作、前后状态和修订、服务器时间、请求编号、影响摘要及可选 reason；本地调用来源不冒充已认证的人审身份。
历史版本、来源、设计文档、检查记录、任务、运行 manifest 和下载继续可读，返回已删除标记；原历史 JSON、事件和工件不就地改写。
不能通过历史读取函数的生命周期检查误挡历史下载；执行受理与只读历史访问使用不同接点。

## 预览与提交

`LifecycleService(root, protected_asset_ids=(), read_only=False)` 是 T03 的服务入口。
`get(ontology_id)` 返回 `{ontology_id,state,revision,protected}`；普通读取不写审计或请求记录。
`events(ontology_id)` 只读返回按成功转移顺序排列的生命周期审计；未知资产不因读取而创建状态行。
`list_assets(state='active')` 默认仅列 active；state='deleted' 查看回收列表，state=None 明确查看全部。
`preview(ontology_id, operation='delete')` 在一致读快照上返回：

```json
{
  "ontology_id": "ont_...",
  "operation": "delete",
  "state": "active",
  "revision": 0,
  "protected": false,
  "can_apply": true,
  "impact": {
    "version_ids": ["ver_..."],
    "sources": [{"source_asset_id": "src_...", "other_asset_count": 1}],
    "runs": [{"run_id": "run_...", "status": "completed"}],
    "sessions": [],
    "legacy_keys": ["built_..."]
  },
  "blockers": [],
  "impact_sha256": "canonical-impact-digest"
}
```

预览不迁移数据库、不登记旧图、不写文件、会话、审计或 token；重复预览的摘要不因读取时间变化。
版本按原登记顺序列出，其余引用使用稳定顺序；HTTP 公开列表每类最多 100 项，并返回 impact_totals 与 has_more，完整引用集仍须进入摘要，不能只校验截断后的清单。
影响依据包括所有版本、来源共享关系、显式引用的运行与会话及其修订／状态、旧 key 映射、治理 head 和证据撤销依据；不把普通文本中的 ID 当作引用。
新增版本／来源引用、运行状态或等待会话修订改变时，即使 lifecycle revision 相同，旧 impact_sha256 也必须失效。
新 check／历史引用的变化同样计入其所属引用清单；公开预览仅回传有界、可定位的引用信息，不暴露原始载荷、路径或凭据。
`delete`／`restore` 均接收 ontology_id、expected_revision、impact_sha256、request_id、confirmed=True 和可选 reason。
expected_revision 必须是非负 int，bool 不算 int；request_id 非空且有界，摘要为 64 位十六进制，confirmed 必须是真布尔值 True。
资产 ID、旧 key 与 request_id 均拒绝路径分隔符、控制字符和空值，最大长度 128；reason 最长 1000 字符，不接受控制字符。
只有提交可以写状态；先完成幂等识别，再复核当前状态、保护、revision、影响摘要和在途冲突。
同一 request_id 的成功重放允许携带当时的旧 revision；它不能绕过新的执行守卫。
无确认返回 confirmation_required；修订冲突 revision_conflict；引用变化 impact_changed；当前状态不允许操作 state_conflict。
不存在 not_found；受保护 protected_asset；排队或执行中 asset_busy；无法安全解析引用 reference_unavailable；只读写入 read_only；存储错误 storage_unavailable。
异常具有稳定 code；拒绝时不改变任何状态、请求记录、审计或文件。

## 受理、冲突和内置保护

queued／running 的生成、构建、适配、设计运行，只要显式固定了该资产或其关联设计会话，就阻止删除，不隐式取消任务。
设计运行的 ontology_id 可以为空，仍需沿 design_input／session_ref／last_asset_ref／output_asset_ref 定位控制依赖；不能只检查 runs.ontology_id。
只共同使用来源文件的独立新构建不是该资产的控制依赖；来源保留，不能误挡与本体无关的构建。
终态运行不阻止删除；等待用户输入的会话不阻止删除，但删除后写入／继续／重试冻结，历史内容保留。
等待会话在 GET 上投影冻结信息，不重写保存的历史；恢复后先重新检查固定版本、head、配置、证据和生命周期修订，不自动排队或恢复旧 ready。
运行受理、排队、claim 和结果提交复核都与删除共享同一数据库事务边界；受理先提交则删除看到 busy，删除先提交则受理拒绝。
统一锁顺序：旧数据入口的 _WRITE_LOCK → _RDF_LOCK → domain.sqlite3 事务；只使用后两者的机器人入口不得反向获取 _WRITE_LOCK。
已有事务中的生命周期检查使用该 conn，不嵌套初始化或另开写事务。
保护依据是服务器内置资产登记及 demo／app 的固定 legacy 身份；不能按用户可编辑的 name、mode='demo' 或客户端传入 protected 决定。
服务器显式传入的 protected_asset_ids 一旦登记保护就持久保留；关闭 UI／功能开关不能解除既有保护或删除守卫。

## 旧 key 兼容和迁移顺序

`bind_legacy(key, name=..., load_effective=..., provenance=..., deleted=False)` 使用服务端注入的回调。
显式快照先读取包含 edits 的有效 IR，并在读取前后比较来源依据；来源改变返回 legacy_changed，不留下资产、版本或工件。
使用现有 legacy_assets 映射维持同一 ontology_id；相同输入不增加版本，active 状态下明确重新登记的新输入可创建派生版本。
该方法仅供显式迁移／编辑，不得在目录 GET、文件扫描、检查 GET 中自动执行。
`resolve_legacy(key)` 返回原 key、固定 ontology_id／version_id、lifecycle 及 restore_available；未知 key 返回 not_found，不替调用方擅自选择 demo。
已删除映射在 bind／扫描中直接保留旧引用，不再调用来源回调，不因原文件重现或变化而创建 active 资产。
迁移必须先固定有效 IR 和 edits，再同事务建立 alias／tombstone；原始 built／forged／edits 文件保留不动。
顺序为：只读盘点／备份 → schema 迁移 → 接入 IR028 删除账本 → 固定尚可读取的旧图快照和映射 → 启用所有入口守卫 → 最后开放 UI 删除／恢复。
IR028 已删除且仍可取得有效快照的旧 key 迁移为 deleted；已物理删除且没有快照的 key 仅保留持久 tombstone，ontology_id／version_id 为空、restore_available=False，不能伪造空本体供恢复。
缺失快照返回 legacy_snapshot_unavailable，提示需从备份恢复；未知、损坏账本或引用不能被当成 active／普通 missing-key fallback。
旧 /api/build/delete 和 forged/delete 委托统一服务，确认／revision／影响依据不足时拒绝，不能保留绕开确认的物理删除分支。
新服务先查统一 alias／tombstone，已删除或已登记 key 不能被原始文件扫描复活；普通从未登记的 missing-key 继续遵守 IR028 的有标注回退。
数据缓存同时纳入统一生命周期修订及现有 IR028 删除依据；恢复只在已启用统一守卫的路径重新开放，旧账本的删除项作为历史保留而不删除文件。
迁移不完整时旧入口继续拒绝已知删除 key；关闭新增展示功能不能使它退回 demo 或绕过状态。

## HTTP 和页面接点（T05 才接通）

| 接口 | 行为 |
| --- | --- |
| GET /api/ontology-assets?state=active\|deleted\|all | 默认 active；与 domain 筛选组合；历史单版本读取不因此禁止 |
| POST /api/ontology-assets/<id>/deletion-preview | 只读 delete 影响预览 |
| POST /api/ontology-assets/<id>/restoration-preview | 只读 restore 预览，包含复查提醒 |
| POST /api/ontology-assets/<id>/delete | 携带 expected_revision、impact_sha256、request_id、confirmed 与可选 reason |
| POST /api/ontology-assets/<id>/restore | 同上，恢复原身份及固定版本 |

状态代码：参数／未确认 400，not_found 404，protected_asset 403，状态／修订／影响／busy 冲突 409，存储或引用解析不可用 503。
读取状态通过 lifecycle 字段和 ETag 暴露；If-Match 与 body expected_revision 同时提供时必须相符，不能只信客户端 ready 或 preview 字段。
HTTP 层拒绝未知写字段与客户端文件路径；预览摘要用于一致性检查，不是授权凭据。
UI 用“移到回收站”“恢复”说明逻辑删除；预览列出版本和引用影响，明确历史保留，确认后才提交。
删除当前选中资产后清空它的活动选择，不自动改选另一份本体；旧历史链接保留并标记已删除，操作按钮不可继续受理。
冲突保留原输入并要求刷新预览；恢复后用户明确选回版本、复查后继续，历史请求重放不自动清空已恢复的选择。

## 任务和验收映射

T02／T03 负责存储服务、稳定预览、确认、CAS、幂等审计、共享来源及历史字节、内置保护、旧 key 快照／tombstone。
T04／T05 再覆盖真实排队竞态、等待会话冻结、所有服务／HTTP 入口、文件扫描、缓存、重启与 UI；T02 不以模拟回调替代这些集成结论。
T06 在工作副本演练迁移和失败回退，核对原版本、来源和历史摘要，完整回归后人审交付。
OAL-01～04 保持原 criterion；测试建立后登记具体映射，未覆盖部分不提前宣称通过。

## T05 版本化启用边界（2026-11-16）

现有 schema 8 数据库在显式服务启动迁移中升级为 schema 9；只读 GET 不执行迁移或登记。
默认启动启用 `DATAMIND_LIFECYCLE_ENABLED=1`，先持久化 migrating 标记，再逐项固定旧 key，成功后记录 ready。
持久化 migrating／ready 标记或已建立绑定的存储根统一执行本规约，开关关闭也不能解除已存在的删除守卫。
迁移中断保留已完成绑定和保护；下次显式启动可继续，不覆盖既有固定版本。
只有尚未显式迁移、没有生命周期标记且没有该 key 绑定的 IR028 存储根保留旧协议，不能将该兼容分支当作已迁移资产的物理清理入口。
`GET /api/asset-lifecycle/capabilities` 暴露当前根是否已进入生命周期管理；正式 UI 的旧 key 删除要求存在固定绑定。
此边界保留既有 IR028 隔离宿主的旧版本契约，正式启动后的 built／forged 删除必须显式确认并委托统一服务。
构建侧栏原有键盘／两次点击防误触保留；第二次进入统一影响预览，不直接执行删除。

显式设置 `DATAMIND_ROBOTICS_ENABLED=0` 的旧宿主默认不自动创建域库存储；需要独立启用数据本体生命周期时明确设置 `DATAMIND_LIFECYCLE_ENABLED=1`。已持久化的绑定和删除守卫不受此启动默认值影响。
