# 持久化运行、事件与工件

依据 DR-064 / DR-065，实现 IR-017 T05 的 Python 服务。
与 AssetStore 使用同一个显式绝对根目录和 `domain.sqlite3`，工件保存到 `artifacts/<run_id>/<artifact_id>/<name>`。
这是存储接口；T07 已提供机器人内核接入及离线演示，后台调度、HTTP/SSE 和新页面由后续阶段完成。

## 创建与查询

```python
from domain_runs import RunStore

runs = RunStore("/absolute/isolated/storage")
# ontology_id 和 version_id 来自同一根目录中 AssetStore 已登记的版本。
run = runs.create_run(
    ontology_id=ontology_id, version_id=version_id,
    operation="inspect", task={"instruction": "检查指定版本"}, mode="real",
    session_ref={"namespace": "ont_chats", "id": "chat-1"},
    components={"profile_id": None, "compiler_version": "example-v1",
                "rule_digest": None, "prompt_version": None,
                "model": {"invoked": False}},
)
runs.start(run["run_id"])
runs.append_event(run["run_id"], kind="progress", payload={"step": "validation"})
history = runs.events(run["run_id"], after=0)
```

输入字典经序列化保存，修改调用方字典不改变历史。
运行固定本体版本及来源摘要，不跟随最新版本；创建、启动、重试和完成时核验固定载荷及来源文件。
会话只存 namespace/id 引用，不复制已有聊天正文；同一会话可关联多个运行。
组件只接受规定字段，model 只允许 invoked/provider/model/configuration_id，不接收密钥或完整供应商配置。
任务、事件、报告由调用方提供业务内容，不应混入连接凭据。

## 完成与文件读取

`finish(run_id, result=..., artifacts=...)` 接收业务结果和文件字节列表。
每个文件包含 `name / kind / media_type / content`，content 必须为非空 bytes；不接收服务器来源路径。
generated 结果恰有一个 kind=candidate 工件，其余业务结果不允许候选；所有结果都必须有 kind=validation 报告。
存储层核验工件数量、路径与摘要，XML 的业务语义校验仍由机器人内核负责；调用方应在校验通过后提交候选。

业务状态 generated / needs_information / needs_clarification / unsupported 对应生命周期 completed；error 对应 failed。
完成时自动创建 manifest.json，记录固定输入、版本/来源、组件、结果与参数依据，以及实际工件标识、类型和摘要；不包含自身摘要。
先独占创建完整文件并复读核验，再将终态、事件和工件索引一起提交；写入或事务失败时数据库保持原状。
失败可留下未登记文件，本阶段不自动清理；这些文件不能通过工件 ID 查询或下载。

`artifacts(run_id)` 列出登记工件，`read_artifact(run_id, artifact_id)` 读取并核验文件。
`manifest(run_id)` 同时校验清单与所有引用工件，文件缺失、被修改或越界符号链接均报错。
文件损坏不改写历史终态；历史曾完成与当前文件能否完整读取是两项信息。

## 重启与重试

构造 RunStore 不自动恢复任务。
仅由单一宿主在启动、接收新工作之前调用 `recover_interrupted()`，将遗留 running 标记为 interrupted，并追加状态事件。
该调用不区分活跃进程，不能在仍有工作者运行时当作定时清理执行；多宿主租约不在本阶段范围内。
queued 和已终止记录不变，重复恢复不产生额外事件。
`retry(run_id)` 只接受 failed/interrupted，按原快照建立新的 queued 记录并保存 retry_of，不覆盖旧记录或复用旧产物。

SQLite 写事务串行分配运行内事件序号；`events(run_id, after=N)` 返回 seq 大于 N 的事件。
完成状态不可再次启动、覆盖或追加进度事件；并发完成只有一个请求提交成功。

## 验证

已审测试：`test_domain_run_store.py`、`test_domain_artifacts.py`、`test_domain_storage_integrity.py`，共 49 项。
这 49 项测试使用合成任务与文件，验证存储行为；T06 另有 30 项已审真实内核接入和跨进程演示测试。
T07 的 `domain_runs.robotics.generate_from_version` 接收指定已发布版本和结构化任务，保存实际候选字节及报告。
`domain_runs.verification.verify_storage` 通过只读连接核验历史及文件，不初始化、迁移或恢复运行。
可直接运行的 CLI、三场景说明和接口用法见 [离线演示](../docs/domain-storage-demo.md)。
审计见 [IR-017 验证记录](../docs/audits/IR-017-storage-validation.md)。
