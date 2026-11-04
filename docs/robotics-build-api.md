# 机器人本体构建 API（IR-018 T05）

T05 接通真实双 XML 上传、后台构建、持久运行、候选版本和下载；T07 已接入页面，操作路径见 [UI 指南](robotics-build-ui.md)。
仅支持 `bt_xml` 配方和 `ur_pick_place` profile，不调用 LLM，不添加演示补充资料，不自动正式发布或执行机器人动作。

## 隔离启动

在工程根目录运行以下命令，使用新临时目录，不修改已有业务存储：

```bash
robotics_demo_root=$(mktemp -d /tmp/datamind-robotics-api.XXXXXX)
printf '%s\n' "$robotics_demo_root"
env DATAMIND_WORKDIR="$robotics_demo_root/work" \
    DATAMIND_DOMAIN_STORAGE_ROOT="$robotics_demo_root/assets" \
    DATAMIND_DB="$PWD/examples/demo_metrics.db" \
    DATAMIND_ENGINE_DIR="$robotics_demo_root/no-engine" \
    DATAMIND_OUTPUTS_DIR="$robotics_demo_root/no-outputs" \
    DATAMIND_ROBOTICS_ENABLED=1 DATAMIND_HOST=127.0.0.1 DATAMIND_PORT=8093 \
    .venv/bin/python -m gunicorn -c gunicorn.conf.py wsgi:application
```

此命令在前台运行，另外打开终端进行下面的请求；端口已占用时使用其他端口并同步调整 URL。
需要重启回查时使用同一临时目录，不能重新执行 `mktemp` 后指向新目录。

## 上传、查询、下载

```bash
curl -sS http://127.0.0.1:8093/api/robotics/capabilities

curl -sS -X POST http://127.0.0.1:8093/api/robotics/builds \
  -F 'name=抓移放候选' -F 'recipe=bt_xml' -F 'profile=ur_pick_place' \
  -F 'request_id=local-build-001' \
  -F 'behavior_tree=@tests/fixtures/robotics/sources/xingweishu.xml' \
  -F 'node_models=@tests/fixtures/robotics/sources/xml_models.xml'
```

响应为 HTTP 202，包含 `run_id`，表示已受理；不能据此判断构建成功。
把实际返回编号替换下面的占位符：

```bash
curl -sS 'http://127.0.0.1:8093/api/domain-runs/<run_id>'
curl -sS 'http://127.0.0.1:8093/api/domain-runs/<run_id>/events?after=0'
curl -sS 'http://127.0.0.1:8093/api/domain-runs?operation=build_ontology&limit=20&offset=0'
curl -sS 'http://127.0.0.1:8093/api/ontology-assets?domain=robotics'
curl -sS 'http://127.0.0.1:8093/api/ontology-assets/<ontology_id>/versions/<version_id>'
```

正常生命周期为 queued → running → completed；成功业务结果 `result.status=built`。
运行中的阶段事件为 computing（包含解析/抽取/协议校验）、committing（保存），不提供虚构的百分比。
`output_asset_ref` 关联新版本；该版本 `publication_state=candidate`，任务适配未评估、机器人执行未验证。
详情中的 `artifacts` 提供本次运行的 OWL、验证报告和 manifest 描述，以及每份文件的 `download_url`、SHA-256 和大小。
直接使用返回的 download_url 下载，例如：

```bash
curl --fail-with-body -sS \
  'http://127.0.0.1:8093/api/domain-runs/<run_id>/artifacts/<artifact_id>' \
  -o /tmp/robotics-candidate.owl
sha256sum /tmp/robotics-candidate.owl
```

文件扩展名请按工件类型选取；SHA-256 应与相应描述一致。失败运行只提供报告和 manifest，没有成功候选 OWL。
下载必须使用同一运行所属的工件 ID；未知/跨运行 ID 为 404，登记文件缺失、损坏或符号链接为 409/artifact_integrity，原历史保持不变。

失败或中断后可重试：

```bash
curl -sS -X POST 'http://127.0.0.1:8093/api/domain-runs/<run_id>/retry' \
  -H 'Content-Type: application/json' -d '{"request_id":"local-retry-001"}'
```

重试创建新运行并保留 `retry_of`，使用原来固定的来源和组件信息；原记录不改。
来源文件已经损坏时不能重试，需重新上传正确内容并使用新的请求 ID。
同一请求 ID 和相同输入返回原运行，跨重启仍有效；改变内容却沿用请求 ID 返回 409/request_conflict。

## 配置与持久化

| 配置 | 默认值 | 约束 |
|---|---|---|
| DATAMIND_ROBOTICS_ENABLED | 关闭 | 支持 1/0、true/false、yes/no、on/off；关闭时新写接口返回 503/feature_disabled |
| DATAMIND_DOMAIN_STORAGE_ROOT | WORK/domain_assets | 显式路径必须为绝对路径 |
| DATAMIND_ROBOTICS_FILE_LIMIT_BYTES | 10485760 | 正整数；总请求继续受平台默认 25 MiB 限制 |
| DATAMIND_ROBOTICS_MAX_PENDING | 4 | 1～64；仅计等待任务，另有一个计算名额 |
| DATAMIND_ROBOTICS_TIMEOUT_SECONDS | 300 | 有限正数，最多 3600 秒；用于计算子进程 |

环境变量供实际服务使用，Python 装配时 Flask app.config 中的同名设置优先。
首版依赖 Linux 本地文件锁，沿用单 worker/gthread 部署；不是分布式执行器，不支持多机器共享存储调度。
不同存储根独立。同一根由唯一宿主管理任务，备用宿主等待取得锁后接管。
计算子进程通过 spawn 启动，不继承宿主执行锁；父进程核对实际结果再持久提交。
正常关闭时停止计算并标记中断；异常退出后，下一个获得执行权的宿主只恢复 build_ontology：running→interrupted，queued 继续调度，终态不重跑。
只读打开存储或读取运行接口不会触发恢复。
关闭功能不创建新库、不启动任务；已有记录不删除，当前 schema 的历史仍可只读查询。

数据库 schema 3→4 只新增 build_requests 去重表与排队索引，不改写既有运行、事件、文件或 manifest。
可写打开会在事务中迁移，失败回滚；只读打开不迁移，未知 schema 拒绝。schema 1/2 继续按顺序升级。
来源注册、去重登记和 queued 运行处于同一数据库事务。失败可能留下无索引文件，但不会出现已登记的半份受理或成功结果；自动清理留后续工作。

## 验证边界

T04 已审的 70 项测试验证服务/HTTP/真实计算子进程/宿主交接与实际应用装配；T05 另有实际 Gunicorn 网络上传、下载和重启回查证据。
T07 已完成新 UI 与实际后端浏览器联动，证据见 [T07 审计记录](audits/IR-018-build-ui-validation.md)；整轮交付仍待 T08 人审。

```bash
.venv/bin/python -m pytest tests/unit/test_domain_build_executor.py tests/unit/test_robotics_build_http.py -q
env PATH="/home/dyh/.local/opt/node-v22.20.0-linux-x64/bin:$PATH" \
  .venv/bin/python scripts/check.py --suite all --report-dir .check-results/robotics-build-api
```

最后一条 PATH 使用当前环境已安装的 Node 22；其他机器应把可用 Node 加入 PATH。
结果与范围见 [IR-018 审计记录](audits/IR-018-build-ui-validation.md)。
