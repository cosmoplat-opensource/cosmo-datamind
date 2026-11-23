# IR-033 · 应用检查提示、统一入口与后退恢复

状态：delivered。关联：[DR-167](../decisions/DR-167-application-guidance-and-return-navigation.md)、[DR-163](../decisions/DR-163-isolated-test-seeds-and-bounded-parallel-checks.md)。

## 目标与验收

- [x] AGN-01：用业务含义解释缺项、影响与解决办法，技术细节可展开且原始诊断保留。
- [x] AGN-02：默认一个“完善本体”入口，可信定义缺项与自定义术语分别进入合适流程，保留其它处理能力。
- [x] AGN-03：修复后退时重新检查并恢复有效操作，禁止自动修复循环、重复任务和绕过过期保护。
- [x] AGN-04：覆盖任务内嵌检查及其它导航入口，记录同类问题的实际排查范围与结果。
- [x] TASK-NET-01：任务读取、保存、生成和 XML 下载超时后恢复操作，离开取消请求；保留输入、请求编号与修订号，显式重试不重复保存、生成或并行下载。

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

## 本轮回归补充

验证基于 `3f0056da8d3441f1d4ce7b2a355c67525e42dc05` 与本次差异；复现前记录版本并保存 `git diff --cached`，Python 3.10.12，依赖采用仓库固定版本。
任务请求期限覆盖响应头及响应体，离开或切换时取消等待，迟到响应不覆盖新状态；保存超时显示“保存结果待确认”，不推断服务端保存失败。
真实浏览器在服务端已保存、前端响应丢失后推进时钟，验证按钮恢复、原请求编号重放、版本和修订号不重复递增；生成响应丢失后同样保留入口，重放不创建第二条生成记录，也不再次调用任务解析模型。
详情用例同步 DR-169 的单一完善入口和提示规则，保留原始执行状态、任务身份、幂等、版本及草稿断言。
设计浏览器准备和并行调整遵循 DR-163；所有 50 项仍执行，原 360 秒限制及三层覆盖率门槛不变。

- 相关单元、组件与规约 203 项通过（49.75 秒），无失败、错误或跳过。
- 设计浏览器 50 项通过，两个独立进程，检查进程耗时 214.555 秒。
- 详情浏览器 29 项通过（158.00 秒）。
- 任务浏览器 23 项通过，检查进程耗时 303.575 秒；保存提示收尾后，保存超时及两类生成重试 3 项复跑通过（42.19 秒，与完整任务组有重叠）。
- 全仓 lint、类型检查及场景基准 55/55 通过；未重新运行全量回归、全仓覆盖率门禁或远端 CI。

以上使用隔离的合成数据、脚本模型响应和真实浏览器／服务；不声明实际模型连通或物理机器人执行通过。
逐次产物不进入产品仓库，复现入口如下；Node 需在 PATH，可使用 Playwright 随包运行时。

```bash
python -m pytest tests/meta tests/unit/test_check_parallel.py tests/unit/test_check_isolation.py tests/unit/test_robotics_seed_support.py tests/unit/test_robotics_task_ui.py tests/unit/test_robotics_task_configuration_ui.py tests/unit/test_robotics_task_completion_feedback.py tests/unit/test_robotics_task_save_feedback.py tests/unit/test_robotics_schema_repair_ui.py tests/unit/test_robotics_schema_repair_guidance.py tests/unit/test_ontology_detail_ui.py -ra
python tests/frontend/robotics-design-browser.py --workers 2
python tests/frontend/ontology-detail-browser.py
python tests/frontend/robotics-task-browser.py
python -m pytest tests/frontend/robotics-task-browser.py -k 'timed_out_saved_completion or unused_profile' -ra
python -m ruff check .
python -m mypy . --no-error-summary
python scripts/scenario_bench.py
```

## 下载与共享夹具修复

验证基于 `c7b191ed4f13f1e2cd4373da14dea4f25f1786c5` 与本次差异；复现前记录版本并保存 `git diff --cached`，环境为 Python 3.10.12、仓库固定开发依赖、Playwright Chromium 与可用 Node。
该提交的全量检查暴露离线 VM 缺少浏览器 API、图谱共享夹具缺少依赖，以及 XML 下载没有总时限；本轮仅修复这些问题，历史全量结果不替代当前差异的验证。
离线 VM 补齐取消和定时 API，先验证任务读取成功，原备注、失败草稿、生成及版本提示断言保留。
图谱套件引入共享夹具的完整依赖，新增 setup-plan 检查在单元入口解析全部依赖；真实浏览器仍执行原有 11 项交互用例。
XML 下载与 JSON 共用 30 秒期限，覆盖响应头、错误响应和文件内容；重复点击只发起一个请求，失败释放控件，离开或任务修订变化取消等待。
下载故障用例覆盖响应头等待、内容读取等待及离开页面，验证迟到结果不生成文件，显式重试得到真实固定版本 XML，原任务、本体版本和模型请求数量不变。

- 相关单元、组件与规约 215 项通过（18.39 秒），包括此前失败的离线 4 项和新的共享夹具检查。
- 下载边界 3 项通过（41.20 秒）；完整任务浏览器 26 项通过，检查进程耗时 340.648 秒，完整组包含该 3 项，不重复累计。
- 真实图谱浏览器 11 项通过，检查进程耗时 44.996 秒；设计浏览器 50 项通过，检查进程耗时 211.491 秒。
- 上述检查均无失败、错误或跳过；相关浏览器仍使用原有 360 秒限制，覆盖率门槛及测试集合不删减。
- 全仓 lint、类型检查和场景基准 55/55 通过；本轮未重新运行全量回归、全仓覆盖率门禁、远端 CI、真实模型或物理设备验证。

浏览器分别通过 `scripts.check.isolated_env` 和 `run_check` 执行，保持临时数据与进程清理。可使用相同仓库依赖独立复现用例：

```bash
python -m pytest tests/meta tests/unit/test_robotics_task_ui.py tests/unit/test_robotics_offline_ui.py tests/unit/test_robotics_task_configuration_ui.py tests/unit/test_robotics_task_save_feedback.py tests/unit/test_robotics_task_completion_feedback.py tests/unit/test_robotics_design_ui.py tests/unit/test_robotics_schema_repair_ui.py tests/unit/test_robotics_schema_repair_guidance.py tests/unit/test_interactive_ontology_graph_ui.py tests/unit/test_interactive_ontology_graph_network.py tests/unit/test_check_parallel.py tests/unit/test_check_isolation.py -ra
python tests/frontend/robotics-interactive-ontology-graph-browser.py
python tests/frontend/robotics-design-browser.py --workers 2
python tests/frontend/robotics-task-browser.py
python -m pytest tests/frontend/robotics-task-browser.py -k xml_download_deadline -ra
python -m ruff check .
python -m mypy . --no-error-summary
python scripts/scenario_bench.py
```

逐次产物仅保留本地或 CI，以上版本、差异和入口为复现依据。

浏览器套件分别启动独立进程，避免同步 Playwright 运行时相互嵌套。
未改变构建语义、SSE 步骤或数据查询规则；本轮机器人场景由上述浏览器和契约用例覆盖，不向数据查询合成库添加无关场景。
逐次日志与截图仅保留本地或 CI；公开复现使用上述版本、差异和命令。

## 任务浏览器的 CI 阶段超时修复

验证基于 `3a69436b250fe64988c05458624935ac40e1767d` 与本次差异，Python 3.10.12、Linux、仓库固定依赖及 Playwright Chromium；浏览器进程限定在两颗可用 CPU 上。
本地资源限制用于验证运行速度变化下的边界，不代表完全复刻远端 Ubuntu／Python 3.11 主机。
任务浏览器复用既有有界并行机制，默认两个独立进程，保留 `--workers 1` 串行对照、原有用例与校验、定向选例和失败退出。
任务直接入口、构建完成、选择器、图谱入口及会话切换按既有 30 秒请求期限等待真实身份与固定版本就绪，再执行页面及生成断言；OWL 导入响应同样明确期限。
新增直接入口 6 秒慢网场景，并为原选择器历史、导入用例加入 6 秒／4 秒传输延迟；只延迟真实网络，不替换业务响应，保留原模式隔离、历史、幂等、来源字节及 XML 校验。
所有原有 Python 断言保留；完整任务套件由 27 项增至 28 项。阶段上限仍为 600 秒，CI 总预算及覆盖率门槛不变。

- 修改前串行 27 项在两颗 CPU 下通过，耗时 458.931 秒；同样限定两颗 CPU、增加两个有界 CPU 竞争进程后，在 600.025 秒被阶段上限终止，仅完成 17 项，复现原 CI 的整组超时。
- 入口约束先出现 6 项预期失败；慢网先复现直接入口、导入和选择器的提前断言。修正后，直接入口／构建／导入 3 项通过（38.65 秒），选择器／图谱／任务切换 3 项通过（47.22 秒）。
- 最终任务套件在两颗 CPU 下 28 项通过，检查进程耗时 277.847 秒，28 份交互证据全部完成断言，无失败、错误或跳过。
- 最终任务套件在同样两颗 CPU、两个有界竞争进程下再次 28 项通过，检查进程耗时 542.431 秒，28 份交互证据全部完成断言；与超时串行基准使用同类资源条件，仍受 600 秒上限约束。
- 相关入口、真实并行失败传播、环境隔离及规约 121 项通过（11.438 秒）；全仓 lint、类型检查及差异空白检查通过。

完整 `scripts/check.py --suite ui` 在调度修订时执行过，9 个阶段均通过；额外强压力检查随后暴露任务准备／导入的短等待，收尾后只修改任务测试，并完整重跑最终 28 项任务套件。
其余 8 个阶段的测试、产品实现与执行参数未变；完整 UI 中有 3 项真实模型连通检查按既有条件跳过，不将其计为执行通过。
本轮没有重新运行项目全量单元、覆盖率门禁或远端 CI，不声明真实模型、物理设备或部署验证。

### 复现

记录 `git rev-parse HEAD` 与候选差异，安装仓库固定开发和 UI 依赖，将 Node 放入 PATH。
串行对照在基准版本的独立副本执行；候选版本执行下列命令，CPU 编号动态选择，不依赖开发者机器路径。

```bash
TEST_CPUS="$(python -c 'import os; print(",".join(map(str, sorted(os.sched_getaffinity(0))[:2])))')"
taskset -c "$TEST_CPUS" python tests/frontend/robotics-task-browser.py --workers 1
taskset -c "$TEST_CPUS" python tests/frontend/robotics-task-browser.py --workers 2
taskset -c "$TEST_CPUS" python scripts/check.py --suite ui
python -m pytest tests/unit/test_check_parallel.py tests/unit/test_check_isolation.py tests/meta -q
python -m ruff check .
python -m mypy . --no-error-summary
git diff --check
```

压力对照在同样两颗 CPU 的亲和性内增加两个独立 Python 计算进程，每个最多运行 620 秒，持续计算 `sum(i*i for i in range(10000))`。
各阶段通过 `scripts.check.isolated_env` 与 `run_check` 执行，仍由原 600 秒限制判定退出码；完成或失败后终止并回收本轮创建的竞争进程和服务。
原始日志、逐项耗时、源文件摘要、候选差异与复现脚本仅保留本地或 CI，公开复现依据为上述版本、资源条件及命令。
