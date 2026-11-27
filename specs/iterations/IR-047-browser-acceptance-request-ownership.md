# IR-047 · 指标图谱切换与浏览器构建、设计夹具

状态：delivered。关联 [DR-186](../decisions/DR-186-browser-acceptance-request-ownership.md) 与 [验收映射](../acceptance.json) 中的 `METRIC-GRAPH-RESPONSE-01`、`METRIC-UI-MODULE-01`、`ROBOTICS-BROWSER-BUILD-OWNER-01` 和 `ROBOTICS-DETAIL-SEED-01`。

## 验收

- [x] 图谱切换期间移除旧表和溯源操作。请求变序时保留当前图谱的指标表、摘要和溯源，已提交的状态变更继续作用于原图谱。
- [x] 历史配置和生成记录的浏览器用例由站点后台完成测试构建，保留原有名称、固定版本 OWL 和生成记录检查。
- [x] 指标列表和溯源由独立脚本加载，HTML 继续满足少于 2500 行的门槛。
- [x] 本体详情用例在站点启动前完成自定义术语候选的准备，映射修复继续指向原固定版本，原 OWL 与版本列表保持一致。

## 验证

指标模块拆分和浏览器夹具修订完成后，本地全量回归共 27 个阶段退出 0。单元与规约 4218 项、集成 536 项、十轮变序 10 项通过。业务名称浏览器 57 项、页面值契约 17 项、机器人构建 19 项、行为树任务 30 项、交互式图谱 15 项和本体详情 29 项通过。场景基准 57 项和本地 Gunicorn 上传回读通过。
验证覆盖迟到的指标响应、由后台完成的构建、站点启动前准备的固定候选，以及本体详情的浏览器行为和持久化断言。抽出指标模块后，`ui/index.html` 为 2435 行，少于 2500 行的原门槛通过。
验证环境为 Linux、Python 3.10、Playwright 1.62、Chrome 156、Node 24 和 JDK 17。可选上游引擎在单元与集成中分别有 5 项和 6 项按条件跳过。浏览器保留项目约定的真实模型连接及 Linux WebKit 构建页跳过项。

## 复现

在[项目测试环境](../../README.md#测试)中运行，确保 Node 可从 `PATH` 调用。

```sh
python -m pytest -q tests/frontend/business_name_browser.py
python -m pytest -q tests/frontend/page_value_contracts.py
python -m pytest -q tests/unit/test_qa_anchor_ui.py::test_index_keeps_anchor_module_out_of_monolith
mkdir -p .check-results
report_dir=$(mktemp -d .check-results/browser-request-ownership-XXXXXX)
python tests/frontend/robotics-build-browser.py --output "$report_dir/build"
python tests/frontend/robotics-task-browser.py --output "$report_dir/task"
python tests/frontend/ontology-detail-browser.py --output "$report_dir/detail"
python scripts/check.py --suite unit --report-dir "$report_dir/unit"
python scripts/check.py --suite integration --report-dir "$report_dir/integration"
python scripts/check.py --suite ui --report-dir "$report_dir/ui"
python scripts/scenario_bench.py
```

Gunicorn 上传回读按 [CI 工作流](../../.github/workflows/ci.yml) 中的 `deployment_smoke` 步骤复现，使用独立工作目录和项目示例库。

指标用例通过调整真实请求的发送顺序检查响应归属。历史配置用例使用构建接口提交 XML，等待后台执行器完成，再读取固定版本。本体详情用例在服务启动前准备固定候选。构建和任务浏览器入口分别生成 JUnit 报告和逐项证据。
