# IR-032 · 机器人构建与行为树应用交互修复

状态：delivered。关联：[DR-166](../decisions/DR-166-robotics-workflow-continuity.md)。

## 目标

修复维护者反馈的任务入口循环、重复知识确认、三处设计页面布局、构建及修订完成出口、类别局部视图恢复和自动执行适配的九项交互问题。

## 交付与任务

- [x] 补充真实浏览器失败测试，覆盖入口、页面顺序、确认与完成导航及局部视图恢复。
- [x] 统一新任务检查入口，自动衔接已允许的执行适配，保留人工角色核对与失败恢复。
- [x] 将知识确认集中到当前关联和参数补齐之后，不默认确认未展示的事实。
- [x] 精简设计页面并将交互移至画布上方，保留只读历史和完整定义。
- [x] 实例化成功后按来源回到图谱或应用检查，并保留原任务及固定版本身份。

## 验收

- [x] RUX-01：本体应用入口先检查，不兼容骨架可进入正确修订，刷新不重复提交。
- [x] RUX-02：关联和参数补齐后仅一次集中知识确认，严格生成和适配依据校验保留。
- [x] RUX-03：类别、关系属性和应用修订的交互均位于画布上方，主视区不重复展示标题框与摘要。
- [x] RUX-04：普通实例化完成进入固定版本图谱，应用修订完成返回对应检查或适配流程。
- [x] RUX-05：单次点击局部关联即可显示完整图谱恢复按钮，并能正确恢复节点。
- [x] RUX-06：可信匹配适配无需重复点击，成功进入任务；不确定、过期、失败与原任务并发更新仍受约束。

## 验证记录

该轮机器人交互修订对应开发快照 `880c981`。历史验证仅覆盖机器人交互范围，未运行全量回归及全仓覆盖率门禁。

先复现任务入口、重复确认、布局、完成导航与局部视图恢复的失败，再实现修复；另补历史查看、旧草稿确认、请求失败和原任务并发更新的失败边界。
旧用例按新的完成导航与确认时点调整操作路径，保留固定版本、只读历史、旧候选字节、真实下载、消费者校验与明确确认的断言。

- 相关单元、HTTP 与组件首轮 201 项通过；后续仅前端收尾，受影响控制器与组件 76 项复跑通过（含新增草稿确认边界，与前轮有重叠，不累加）。
- 最终设计浏览器 41 项通过，任务浏览器 13 项通过，图谱浏览器 10 项通过。
- 收尾同步已消费的修订返回参数及继续意图后，两种修订返回路径复跑 2 项通过；其它产品代码未再修改。
- 规约检查 95 项通过；场景基准 55/55；全仓 lint、涉及 Python 文件的类型检查和差异空白检查通过。
- 上述检查无失败、错误或跳过；浏览器使用真实 Chromium、HTTP 服务、持久化及生成/下载链路，仅模型响应由合成测试服务提供，不验证真实模型或机器人现场执行。

### 复现环境与命令

使用仓库固定开发与 UI 依赖，Python 3.10+、Playwright Chromium；Node 需在 PATH 中，可使用 Playwright 随包 Node。
在隔离运行目录中执行，勿加载真实模型凭据或业务运行目录；标准隔离方法见 `scripts/check.py` 的 `isolated_env`。

```bash
python -m ruff check .
python -m mypy tests/frontend/robotics-design-browser.py tests/frontend/robotics-task-browser.py tests/frontend/robotics-interactive-ontology-graph-browser.py tests/unit/test_robotics_task_ui.py --no-error-summary
python -m pytest tests/meta/ -ra
python -m pytest tests/unit/test_robotics_design_ui.py tests/unit/test_robotics_schema_repair_ui.py tests/unit/test_robotics_task_ui.py tests/unit/test_robotics_task_configuration_ui.py tests/unit/test_robotics_task_save_feedback.py tests/unit/test_robotics_repair_stages.py tests/unit/test_robotics_schema_repair_guidance.py tests/unit/test_robotics_application_workflow.py tests/unit/test_robotics_automatic_adaptation.py tests/unit/test_robotics_application_http.py tests/unit/test_robotics_task_completion.py tests/unit/test_robotics_task_http.py tests/unit/test_robotics_task_configurations.py tests/unit/test_interactive_ontology_graph_ui.py tests/unit/test_interactive_ontology_graph_network.py tests/unit/test_interactive_ontology_graph_http.py tests/unit/test_robotics_task_fixture_controls.py -ra
python tests/frontend/robotics-design-browser.py
python tests/frontend/robotics-task-browser.py
python tests/frontend/robotics-interactive-ontology-graph-browser.py
python scripts/scenario_bench.py
```

构建语义、SSE 步骤与数据问数均未修改，因此不改动数据查询合成库；新增机器人场景由上述真实浏览器与原有后端用例覆盖。
逐次日志与截图仅保留本地或 CI，公开复现使用以上实现版本与命令。
