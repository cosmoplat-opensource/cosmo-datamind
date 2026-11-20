# IR-036 · 最终任务配置校验

状态：delivered。关联：[DR-170](../decisions/DR-170-task-configuration-validation-scope.md)。

## 任务与验收

- [x] CFG-SCOPE-01：切换到未补齐配置、部分保存后再切回原配置，按最终配置完成原有确认并生成正确行为树，保留草稿及历史版本。
- [x] 当前配置及共享依赖缺项、错误值、执行结构损坏、规则篡改和版本错误仍阻断候选；完整发布校验不放宽。
- [x] GEN-FEEDBACK-01：任务阶段提供针对当前配置的补齐与重试，不要求为未选中草稿完善整个本体；历史错误可显式重试且不重复模型请求。
- [x] 先运行失败测试，完成相关回归后更新状态并暂存。

## 实现与兼容

任务适配与全本体发布校验分开：在完整原图上只为四类配置规则限定目标节点，规则约束本身不变，执行结构、技能、端口、绑定、数据流与来源验证保持原范围。
目标包括最终关联的感知与放置配置，以及所有直接引用的关节值和目标位姿；共享依赖仍检查，不删除未选中配置或过滤失败报告。
任务加载及生成复验使用同一任务，跨任务复用校验上下文被拒绝；原图、原始字节摘要、历史版本及应用来源 JSON／OWL 同步契约保持。
任务候选报告标明 `validation_scope.kind=task_configuration` 与配置节点列表；未传任务的完整加载和发布仍检查所有配置，不把任务候选解释为整个本体已可发布。
历史错误保留原记录，页面提供按当前配置重新生成的显式入口；重试继续原服务端修订号、幂等与生成校验，不重新调用模型。
当前配置缺项继续本任务表单补齐，未选中配置的残缺不再要求用户修改本体。

## 验证

验证基于 `880c98197d80ef80744c7f849b657862eb0afe32` 加本次差异；复现前保存 `git diff --cached` 标识候选内容。
先用失败测试复现切回后仍被草稿阻断、缺少任务校验上下文及旧错误仍跳转本体维护的问题，再实现范围修订。
上轮“未选中配置必须阻断任务”的断言按维护者新要求调整，同时保留默认完整加载必须拒绝、业务诊断、原始字节及历史数据不变的验证。
RDF 重新解析的空白节点标识不稳定，语义保持使用同构比较，并额外核对原始字节，而不是比较新解析对象的节点标识。

- 最终配置范围单元 8 项通过（41.360 秒），覆盖感知／放置切回、部分保存、共享依赖、规则及结构破坏、任务错配和完整发布边界。
- 任务浏览器 21 项通过（268.871 秒），包括两类配置的真实编辑切换、原参数复用、独立 XML 标准答案、草稿保留、重新加载及历史失败重试。
- 应用工件兼容检查 21 项通过（21.322 秒）；应用来源的任务切换、固定版本 JSON／OWL 同步和来源保留也纳入最终相关回归。
- 候选集合规约检查 95 项通过（9.755 秒），场景基准 55/55；全仓 lint、涉及 Python 文件的类型检查及差异空白检查通过。
- 最终相关单元及 HTTP 回归 175 项通过（314.484 秒，4 个有界工作进程），无失败、错误或跳过。

范围单元与最终相关组存在重复，不累加为独立用例。
上述浏览器及自动化验证使用合成业务资产与模型响应，不连接真实模型或机械臂，不声明实际现场执行通过。
未运行全量回归与全仓覆盖率门禁。

## 复现

安装仓库固定开发及 UI 依赖、Playwright Chromium，Node 必须在 PATH，可使用 Playwright 随包 Node。
使用 `scripts/check.py` 的 `isolated_env` 构造临时工作区；浏览器套件独立进程执行。

```bash
python -m pytest tests/unit/test_robotics_task_scope.py tests/unit/test_robotics_task_generation_feedback.py tests/unit/test_robotics_task_configurations.py tests/unit/test_robotics_task_service.py tests/unit/test_robotics_task_http.py tests/unit/test_robotics_publication.py tests/unit/test_robotics_baseline.py tests/unit/test_robotics_offline_candidates.py tests/unit/test_robotics_application_workflow.py tests/unit/test_robotics_task_ui.py tests/unit/test_robotics_task_configuration_ui.py tests/unit/test_ontology_asset_versions.py -n 4 --dist worksteal -q
python -m pytest tests/frontend/robotics-task-browser.py -q
python -m pytest tests/unit/test_robotics_application_artifacts.py -q
python -m pytest tests/meta -q
python -m ruff check .
python -m mypy domains/robotics/artifact_contract.py domains/robotics/consumer.py domains/robotics/generation.py domains/robotics/task_knowledge.py domain_runs/robotics.py domain_runs/robotics_task_service.py ontology_assets/store.py tests/unit/test_robotics_task_scope.py tests/unit/test_robotics_task_generation_feedback.py tests/unit/test_robotics_application_workflow.py tests/frontend/robotics-task-browser.py --no-error-summary
python scripts/scenario_bench.py
```

逐次运行日志、截图及候选差异只留本地或 CI；以上版本、差异和命令为公开复现入口。

## 重试无变化的运行态复核

2026-11-20：失败任务的修订号在重试后递增，但服务仍返回旧异常模板；前端文件已更新，常驻 Python 工作进程没有重新加载修复后的生成代码。
同一失败任务在隔离存储副本中使用当前代码显式重试，成功生成候选，保留本体固定版本，修订号递增一次，未调用模型。
确认本机没有运行中的构建或任务后，通过已有服务管理器重新加载同一监听地址、工作目录与数据库；健康检查通过，`source_changed=false`。
重新加载后以实际 HTTP 只读读取原失败任务，任务、本体版本及修订号保持一致；原任务没有被自动重跑或修改，用户可刷新页面后显式重试。
本轮未新增 DR、未变更业务生成逻辑；补充验证沿用本记录的实现范围。历史规约通过不代表当时的常驻服务已经加载该版本。

- 服务管理及最终配置范围定向回归 17 项通过（41.523 秒）。
- 历史错误重试、感知与放置配置切回的真实浏览器回归 3 项通过（53.782 秒），无失败、错误或跳过。
- 本轮没有运行全量回归；原任务的隔离重放及本机服务重载不代表模型或物理设备执行验证。

补充复现入口：

```bash
python scripts/service.py status
python scripts/service.py restart
python -m pytest tests/unit/test_service_manager.py tests/unit/test_robotics_task_scope.py -q
python -m pytest tests/frontend/robotics-task-browser.py -k 'unused_profile_is_preserved or switch_back_from_unfinished_configuration' -q
```

服务重载记录、隔离任务副本的运行结果及 HTTP 核对结果只保留本地；不将用户任务数据、运行目录或模型配置发布到仓库。
