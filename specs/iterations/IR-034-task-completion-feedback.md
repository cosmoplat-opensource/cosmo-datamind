# IR-034 · 任务补齐反馈与完善方式说明

状态：delivered。关联：[DR-168](../decisions/DR-168-task-completion-feedback.md)。

## 目标与任务

- [x] TCF-01：其它完善方式说明已有定义的适用情形、人工核对和后续检查，保留原入口。
- [x] TCF-02：未确认知识、未选对象、对象名称空白、参数空白、缺少适配依据或有效期给出对应提示；错误不丢输入，合法分步保存及修正重试可用。

## 实现与验证

先运行单元及真实浏览器用例，复现空提交的泛化提示、空白值缺少具体引导及分步保存缺少剩余提示，再实现修复。
前端依据当前可见缺项做针对性提示；服务端仍独立校验，空答案根据当前可信缺项说明下一步，错误码及写入失败语义保持不变。
不自动确认知识或适配证据，合法分步填写及未确认依据草稿继续可保存；非法数值仍由原有领域校验拒绝。

- 任务浏览器 18 项通过；新增参数空白断言后，桌面及窄屏参数用例 2 项复跑通过。
- 应用说明与两条返回路径浏览器 4 项通过；说明文字收尾后，两种宽度 2 项复跑通过。
- 控制器及保存反馈单元 36 项复跑通过。以上复跑包含重复覆盖，不累加为独立用例。
- 相关服务、补齐、HTTP、组件首轮共 138 项，其中 137 项通过，首次 HTTP 模型调用用例失败。原因是先导入引擎模块再首次导入服务时，启动配置清除了测试临时注入的模型配置；同项单独运行通过。
- 测试夹具调整为先初始化服务再注入本项模型配置，原有请求、生成结果和下载字节断言不变；HTTP 套件及前置模型初始化用例按原触发顺序复跑 20 项通过，无失败、错误或跳过。
- 候选文件集合的规约检查 95 项通过，场景基准 55/55；全仓 lint、涉及 Python 文件的类型检查及差异空白检查通过。
- 正常与边界验证采用真实浏览器、服务及持久化数据；模型使用合成响应，不代表真实模型或设备现场验证。未执行全量回归及全仓覆盖率门禁。

发布链接检查须包含新 DR、IR 文件的候选集合；漏纳入候选文件时会报告未发布引用。
Node 必须在 PATH 中，可使用 Playwright 随包 Node；缺少 Node 的可选环境跳过不作为通过证据。

## 复现

安装仓库锁定的开发及 UI 依赖、Playwright Chromium；使用 `scripts/check.py` 的 `isolated_env` 构造临时工作区，避免继承真实模型凭据和业务资产。
浏览器套件分别在独立进程运行。

```bash
python -m pytest tests/unit/test_robotics_task_service.py tests/unit/test_robotics_task_completion.py tests/unit/test_robotics_task_http.py tests/unit/test_robotics_task_save_feedback.py tests/unit/test_robotics_task_completion_feedback.py tests/unit/test_robotics_task_ui.py tests/unit/test_robotics_task_configuration_ui.py -q
python -m pytest tests/unit/test_robotics_task_service.py::test_session_and_result_survive_fresh_process_and_new_session_is_empty tests/unit/test_robotics_task_http.py -q
python -m pytest tests/unit/test_robotics_task_completion_feedback.py tests/unit/test_robotics_task_ui.py tests/unit/test_robotics_task_save_feedback.py -q
python -m pytest tests/frontend/robotics-task-browser.py -q
python -m pytest tests/frontend/robotics-design-browser.py -k 'application_guidance_explains or back_from_each_repair' -q
python -m pytest tests/meta -q
python -m ruff check .
python -m mypy domains/robotics/task_knowledge.py tests/unit/test_robotics_task_completion_feedback.py tests/unit/test_robotics_task_http.py tests/frontend/robotics-task-browser.py tests/frontend/robotics-design-browser.py --no-error-summary
python scripts/scenario_bench.py
```

逐次运行日志、候选差异及截图只留本地或 CI，仓库保留以上复现方法。

## 后续入口修订

2026-11-20：维护者要求移除其它完善方式。TCF-01 当前范围及映射转由 [IR-035](IR-035-single-repair-entry-and-generation-feedback.md) 和 [DR-169](../decisions/DR-169-single-repair-entry-and-generation-feedback.md) 记录；本轮入口说明仅为历史结果，保存反馈 TCF-02 保持。
