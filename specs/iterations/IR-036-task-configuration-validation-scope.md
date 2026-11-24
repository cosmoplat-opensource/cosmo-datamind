# IR-036 · 最终任务配置校验

状态：delivered。关联：[DR-170](../decisions/DR-170-task-configuration-validation-scope.md)。

## 任务与验收

- [x] CFG-SCOPE-01：切换到未补齐配置、部分保存后再切回原配置，按最终配置完成原有确认并生成正确行为树，保留草稿及历史版本。
- [x] 当前配置及共享依赖缺项、错误值、执行结构损坏、规则篡改和版本错误仍阻断候选；完整发布校验不放宽。
- [x] GEN-FEEDBACK-01：任务阶段提供针对当前配置的补齐与重试，不要求为未选中草稿完善整个本体；历史错误可显式重试且不重复模型请求。

## 实现与兼容

任务适配与全本体发布校验分开：在完整原图上只为四类配置规则限定目标节点，规则约束本身不变，执行结构、技能、端口、绑定、数据流与来源验证保持原范围。
目标包括最终关联的感知与放置配置，以及所有直接引用的关节值和目标位姿；共享依赖仍检查，不删除未选中配置或过滤失败报告。
任务加载及生成复验使用同一任务，跨任务复用校验上下文被拒绝；原图、原始字节摘要、历史版本及应用来源 JSON／OWL 同步契约保持。
任务候选报告标明 `validation_scope.kind=task_configuration` 与配置节点列表；未传任务的完整加载和发布仍检查所有配置，不把任务候选解释为整个本体已可发布。
历史错误保留原记录，页面提供按当前配置重新生成的显式入口；重试继续原服务端修订号、幂等与生成校验，不重新调用模型。
当前配置缺项继续本任务表单补齐，未选中配置的残缺不再要求用户修改本体。

## 验证

最终任务配置校验对应开发快照 `3f0056d`。
先用失败测试复现切回后仍被草稿阻断、缺少任务校验上下文及旧错误仍跳转本体维护的问题，再实现范围修订。
上轮“未选中配置必须阻断任务”的断言按维护者新要求调整，同时保留默认完整加载必须拒绝、业务诊断、原始字节及历史数据不变的验证。
RDF 重新解析的空白节点标识不稳定，语义保持使用同构比较，并额外核对原始字节，而不是比较新解析对象的节点标识。

验证范围包括配置切换与草稿保留、应用工件的 JSON／OWL 同步、候选版本及任务 HTTP 接口、浏览器生成和 XML 下载。浏览器使用合成资产与模型响应，验证范围限于软件行为。
该阶段记录了 175 项相关单元与 HTTP 检查、21 项任务浏览器用例及 55 项场景基准通过。全仓回归和覆盖率门禁不在该次验证范围内。

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

## 历史任务的显式重试

读取已有失败任务时保留本体固定版本和修订号。刷新页面只读取；用户显式选择按当前配置重试后，服务端沿用请求编号进行幂等检查。以下用例在隔离存储中检查旧错误恢复、配置切回及显式重试的运行次数。

```sh
python -m pytest tests/frontend/robotics-task-browser.py -k 'unused_profile_is_preserved or switch_back_from_unfinished_configuration' -q
```

## 生成重试的响应期限

开发快照 `3a69436` 中的测试修订将生成响应的等待期限对齐到产品请求的 30 秒。普通控件的等待期限是 3500 毫秒，不能用来判断一次生成请求已超时。
慢响应场景引入 4 秒传输延迟，使用真实 HTTP 响应和独立 XML 标准答案；响应丢失后的显式重试继续校验请求编号、修订号、固定版本及模型调用次数。该修订的验证覆盖任务浏览器与相关单元、规约检查，未运行全量回归。
