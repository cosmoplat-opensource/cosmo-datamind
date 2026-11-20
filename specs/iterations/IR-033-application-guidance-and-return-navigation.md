# IR-033 · 应用检查提示、统一入口与后退恢复

状态：delivered。关联：[DR-167](../decisions/DR-167-application-guidance-and-return-navigation.md)。

## 目标与验收

- [x] AGN-01：用业务含义解释缺项、影响与解决办法，技术细节可展开且原始诊断保留。
- [x] AGN-02：默认一个“完善本体”入口，可信定义缺项与自定义术语分别进入合适流程，保留其它处理能力。
- [x] AGN-03：修复后退时重新检查并恢复有效操作，禁止自动修复循环、重复任务和绕过过期保护。
- [x] AGN-04：覆盖任务内嵌检查及其它导航入口，记录同类问题的实际排查范围与结果。

## 任务

- [x] 编写并运行提示、默认路径和真实历史导航的失败测试。
- [x] 实现业务提示、折叠细节、统一入口及返回时的检查刷新。
- [x] 检查按钮锁定、异步迟到响应及相关页面前进/后退行为。
- [x] 完成相关回归，更新状态并暂存。

## 验证

基于 `880c98197d80ef80744c7f849b657862eb0afe32` 加本次暂存差异；未运行全量回归及全仓覆盖率门禁，未提交、推送或部署。

先用失败测试复现提示、默认入口和两条修复路径返回后禁用的问题，再实现修复；补测并复现异常返回、修订冲突、任务版本变化、内嵌检查恢复、旧链接及请求超时边界。
业务提示与旧版技术标识的断言按本次展示约定更新，保留原始报告、约束、固定版本、任务草稿和拒绝过期动作的验证。

- 相关契约、HTTP 与组件首轮 291 项通过（155.315 秒）；收尾后控制器与组件 78 项复跑通过（2.793 秒，与前轮有重叠，不累加）。
- 设计浏览器完整 49 项通过（402.035 秒）；新增超时及收尾恢复后，相关设计导航 14 项复跑通过（98.796 秒，含虚拟时钟推进的超时用例）。
- 任务浏览器 17 项通过（199.723 秒）；收尾请求边界更新后，内嵌检查与任务版本恢复 4 项复跑通过（43.60 秒）。
- 图谱浏览器 11 项通过（43.579 秒）；构建离开/恢复及知识评审未保存修改导航各 1 项通过。
- 规约检查 95 项、场景基准 55/55、全仓 lint、涉及 Python 文件的类型检查及差异空白检查通过。
- 上述检查无失败、错误或跳过；使用真实浏览器、服务、固定版本与持久化，仅模型响应和网络异常边界采用合成测试条件，不验证真实模型或机器人现场执行。

### 同类问题排查结果

1. 修订设计会更新设计修订号，核对已有定义会更新适配记录，两者都会使旧检查过期；返回时现在重新检查并保留原版本和任务范围，停留在诊断页。
2. 修复响应不符合导航约定、请求失败或超时会恢复操作；30 秒超时不自动重试，显式重试复用请求编号。
3. 其它窗口更新设计后，旧动作被服务端拒绝时保留过期保护，并提供可操作的重新检查入口。
4. 内嵌检查补上重新检查入口；任务已换版本时，返回检查和打开旧任务链接均提供显式的当前任务入口，保留原来的身份检查。
5. 迟到响应不能把用户从已离开的页面带回；任务返回保留未保存选择，图谱返回控件可用，构建离开停止轮询且恢复不重复提交，知识评审仍要求明确处理未保存修改。

源码排查覆盖应用检查、机器人设计/适配/任务/历史/构建/图谱/评审、目录、生命周期、数据库选择、构建预处理，以及主路由和共享请求按钮锁定逻辑。
本轮确认并修复以上同源问题，导航用例通过的其它路径保持原实现；不将此范围内的检查解释为全系统不存在其它问题。

### 复现

安装仓库固定开发与 UI 依赖，使用 Python 3.10+、Playwright Chromium，并将 Node 放入 PATH；可使用 Playwright 随包 Node。
使用 `scripts/check.py` 的 `isolated_env` 构造隔离环境，不加载真实模型凭据或业务目录。记录 `git rev-parse HEAD` 和 `git diff --cached` 后执行：

```bash
python -m ruff check .
python -m mypy application_contracts/adapters.py application_contracts/robotics_checks.py tests/unit/test_application_repair_recommendation.py tests/unit/test_robotics_task_ui.py tests/unit/test_robotics_schema_repair_ui.py tests/frontend/robotics-design-browser.py tests/frontend/robotics-task-browser.py tests/frontend/robotics-interactive-ontology-graph-browser.py --no-error-summary
python -m pytest tests/meta/ -ra
python -m pytest tests/unit/test_application_repair_recommendation.py tests/unit/test_robotics_schema_repair_ui.py tests/unit/test_robotics_schema_repair_guidance.py tests/unit/test_application_contract_registry.py tests/unit/test_application_contract_checks.py tests/unit/test_application_contract_service.py tests/unit/test_application_contract_http.py tests/unit/test_application_contract_freshness.py tests/unit/test_application_contract_host.py tests/unit/test_robotics_automatic_adaptation.py tests/unit/test_robotics_application_http.py tests/unit/test_robotics_design_ui.py tests/unit/test_robotics_task_ui.py tests/unit/test_robotics_review_ui.py -ra
python tests/frontend/robotics-design-browser.py
python tests/frontend/robotics-task-browser.py
python tests/frontend/robotics-interactive-ontology-graph-browser.py
python -m pytest tests/frontend/robotics-build-browser.py -k leaving_page_stops_polling -ra
python -m pytest tests/frontend/robotics-review-browser.py -k unsaved_navigation_requires_explicit_discard -ra
python scripts/scenario_bench.py
```

浏览器套件分别启动独立进程，避免同步 Playwright 运行时相互嵌套。
未改变构建语义、SSE 步骤或数据查询规则；本轮机器人场景由上述浏览器和契约用例覆盖，不向数据查询合成库添加无关场景。
逐次日志与截图仅保留本地或 CI；公开复现使用上述版本、差异和命令。
