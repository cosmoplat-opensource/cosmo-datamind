# 本体资产与版本存储

依据 DR-059 / DR-064 / DR-065，实现 IR-017 T03 的 Python 服务。
该模块尚未接入新 HTTP 路由或 UI；旧接口与聊天存储保持原有入口。

## 用法

```python
from pathlib import Path
from ontology_assets import AssetStore

store = AssetStore(Path("/absolute/isolated/storage"))
asset = store.create_asset(name="生产数据", domain="data")
version = store.add_version(
    asset["ontology_id"],
    b'{"objects": [], "relations": []}',
    payload_kind="datamind_ir",
)
ir = store.load_data_ir(asset["ontology_id"], version["version_id"])
```

存储根必须是显式绝对路径；模块不导入 server，也不从用户工作目录自动迁移图谱。
根目录中的 `domain.sqlite3` 保存资产、来源、版本、旧目录关联以及运行/事件/工件索引，`sources/` 保存来源原始字节，`versions/` 保存版本原始字节。
数据库 schema 当前为 2；打开 schema 1 时在事务内添加运行表，保留原有数据；未知版本或已有但没有本模块 schema 的数据库明确拒绝初始化，不静默清空。
每个调用打开并关闭自己的连接，写入使用 SQLite 事务；先创建完整文件并核验摘要，再提交可见索引。
失败可能留下未登记文件；目前不自动删除这些文件，避免误删历史工件；未登记文件不会成为可查询或可下载的工件。

## 载荷与发布

- data 资产使用 datamind_ir，保留输入 JSON 字节；兼容 objects 与 links/relations 形状，数据读取入口拒绝机器人载荷。
- robotics 资产使用 robotics_rdf，保留完整 RDF/XML 字节；候选登记只检查语法，不将候选当作已经通过发布校验。
- 新版本使用新标识和新文件，不提供原地覆盖接口；父版本必须属于同一资产，来源引用必须存在且摘要相符。
- `publish_version(ontology_id, version_id, mode="real")` 当前用于机器人版本，复用 IR-016 消费契约与可信规则，保存实际验证结果。
- `load_robotics(...)` 只消费已发布且模式一致的版本，每次核对摘要并复验；新版本出现不切换调用方指定的旧版本。
- 发布/加载使用与 server 相同的 RDF 锁；锁只包围本体校验，不包含外部模型调用。
- 文件入库或发布通过不会补齐任务证据，也不表示机器人执行已验证。

`metadata` 是调用方提供的来源说明，不能授予发布状态；顶层 `validation` 只由实际发布校验写入。
元数据与查询返回值均与调用方对象分离，修改字典不改变存储记录。
版本按资产 ID 与版本 ID 共同定位，不建立跨资产合并的全局 IRI 表。

## 旧目录登记

`ontology_assets.legacy.register_legacy()` 接收目录枚举、有效 IR 读取和来源摘要三个回调。
宿主可注入现有 `load_ir_edited()`；该模块本身不读取原始 IR 来替代有效编辑结果。
每次显式登记准备完整快照后，在单个事务中保存；读取期间来源摘要改变则拒绝本次登记。
相同旧 key、有效载荷及来源摘要重复登记复用原版本；内容或来源摘要改变则创建子版本。
不修改旧 IR、编辑日志、聊天和 JOBS，不替代旧问数路径。
回调由宿主提供，宿主应提供完整来源/编辑摘要；此处不把摘要字段视为真实业务证据。

## 验证与剩余工作

已审测试位于 `tests/unit/test_ontology_asset_catalog.py`、`test_ontology_asset_versions.py`、`test_legacy_asset_adapter.py`。
运行、事件、会话关联、manifest、显式重启恢复已由 IR-017 T05 的 [domain_runs](../domain_runs/README.md) 实现，并通过 T04 的故障与并发测试。
真实机器人内核与存储的整链路接入、跨进程演示仍由 T06/T07 承接。
平台 UI 与前后端联动按 Proposal 步骤 3～7 承接。
