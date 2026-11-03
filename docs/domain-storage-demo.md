# 本体与运行存储离线演示

对应 IR-017 T07、DR-059 / DR-064 / DR-065。
这套命令从仓库内两份 XML 新构建 OWL，接入不可变版本、严格生成、持久化运行和工件清单，并通过独立 Python 进程核验。
不需要启动 UI、配置模型、读取原 yang 归档或连接机器人。
任务是固定的六字段结构化输入；尚不包含自然语言任务解析。

## 直接运行

在工程根目录执行：

```bash
.venv/bin/python scripts/demo_domain_storage.py --case all --output-dir .check-results/domain-storage-demo
```

命令会打印本次运行目录、存储目录、各场景结果及 summary.md 路径。
只有所有场景符合预期、独立进程完整性核验也通过时退出 0；输入错误、运行错误、核验失败均退出非零。
每次在输出目录下新建带时间和随机后缀的子目录，不覆盖前次结果。
也可从任意目录使用解释器与脚本的绝对路径运行。

| 场景 | 展示内容 | 预期 |
|---|---|---|
| demo-success | 登记数据 IR 和新构建的机器人 OWL；模拟资料作为来源保存；创建阈值为 0.61 的新版后，指定旧版生成，旧版仍使用 0.72 | generated，候选带 DEMO 标记 |
| real-missing-evidence | 不向本体应用演示证据；完成运行并保存真实知识缺口报告 | needs_information，无候选 |
| interrupted-recovery | 子进程提交 running 后以退出码 23 结束；另一个子进程显式恢复，重复恢复无额外变化 | interrupted，无候选 |

三种场景共用本次唯一的存储根，展示双领域目录、固定旧版本、拒绝结果与中断历史。
模拟资料不能视作真实标定或现场验证；候选的 execution_readiness 始终为 not_verified。

## 单独运行某个场景

```bash
.venv/bin/python scripts/demo_domain_storage.py --case demo-success --output-dir .check-results/domain-storage-demo
.venv/bin/python scripts/demo_domain_storage.py --case real-missing-evidence --output-dir .check-results/domain-storage-demo
.venv/bin/python scripts/demo_domain_storage.py --case interrupted-recovery --output-dir .check-results/domain-storage-demo
```

默认来源为 `tests/fixtures/robotics/sources/xingweishu.xml` 和 `xml_models.xml`。
默认补充资料为 `tests/fixtures/robotics/demo_supplement.json`；可以用 `--source / --models / --supplement` 指定替代文件。
demo 模式会校验补充资料与输入 XML 的摘要是否匹配；替代输入不受支持时明确失败，不回退到预制 OWL。
real 场景只从补充资料文件读取 task 六字段，不应用其中的参数和模拟证据，也不将其登记为真实证据来源。
构建使用本次读取的文件字节副本，登记来源与实际构建输入一致，结束后清理临时构建目录。

## 查看交付文件

本次输出结构如下：

```text
<unique-run-directory>/
  summary.json
  summary.md
  storage/
    domain.sqlite3
    sources/<source-id>.bin
    versions/<...>.owl 或 .json
    artifacts/<run-id>/<artifact-id>/demo_candidate.xml
    artifacts/<run-id>/<artifact-id>/validation.json
    artifacts/<run-id>/<artifact-id>/manifest.json
```

`summary.md` 列出场景结果、运行 ID、问题及可打开的工件链接。
`summary.json` 记录 storage_root、生产进程 PID、独立核验进程 PID、资产/运行 ID；中断场景还包含崩溃和恢复进程 PID。
`validation.json` 是该运行的业务结果，包含状态、问题、参数依据和实际验证结果。
`manifest.json` 关联固定输入、版本、来源、组件信息和各工件摘要，不包含自身摘要。
正式结果去掉内核临时 candidate_path；调用方通过 run_id 与 artifact_id 查询候选，不能依赖已清理的临时文件路径。
数据本体为明确标注的演示 IR；本命令不会登记、迁移或修改用户当前业务图谱及聊天文件。

## 在新的进程中重新核验

复制终端或 summary.json 中的 storage_root，替换下例路径：

```bash
.venv/bin/python scripts/demo_domain_storage.py --verify-storage /absolute/path/to/storage
```

输出 JSON，包括 passed、pid、run_ids 和 assets；退出 0 表示本次文件及历史完整性检查通过。
核对数据库 schema/约束、来源与版本字节、运行固定快照、事件序号、manifest 和登记工件的摘要及长度。
核验不重新生成 XML，不重新执行本体语义校验，也不证明现场机器人执行能力。
核验使用 SQLite mode=ro；不存在或空目录失败，不创建数据库、不执行迁移、不触发运行恢复。
若运行仍为 running，只检查其已保存记录，不将其改为 interrupted。
适合核验本演示已停止写入的存储；正在发生写入的存储可能在分段读取期间变化，应在稳定后重新核验。

## Python 接入

```python
from domain_runs.robotics import generate_from_version

run = generate_from_version(
    "/absolute/path/to/storage",
    ontology_id="从 AssetStore 取得的 ID",
    version_id="该资产已发布的版本 ID",
    task={"operation": "pick_place", "part": "Part_A_001", "station": "Station_B",
          "carrier": "Carrier_B_01", "slot": "Slot_B_01", "unsupported_requests": []},
    mode="demo",
    session_ref={"namespace": "ont_chats", "id": "chat-1"},
)
```

必须指定已发布、模式一致的机器人版本；无效版本在创建运行前拒绝。
生命周期与业务结果分开：缺信息等业务拒绝记为 completed 并保留报告，内核意外异常记为 failed。
持久化提交失败向调用方抛异常，不能假装运行完成；遗留 running 由宿主启动时显式恢复。
组件快照记录实际 compiler.py 的字节摘要和消费规则摘要；编译器文件摘要不代表整个环境的版本。
该接口不包含调度队列、自然语言模型调用、HTTP 下载或新 UI，这些由后续集成阶段承接。
