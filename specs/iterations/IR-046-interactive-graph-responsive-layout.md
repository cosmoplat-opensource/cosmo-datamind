# IR-046 · 密集图谱的布局与点击响应

状态：delivered。关联 [DR-185](../decisions/DR-185-interactive-graph-responsive-layout.md) 和 [GRAPH-LAYOUT-RESPONSE-01](../acceptance.json)。

## 验收

- [x] 布局期间标签与引线随连线移动，布局结束或暂停后按当前几何恢复避让。
- [x] 在 Chromium 降速环境下，从 601 条连线的超限图谱进入局部范围，布局运行期间按原点击时限操作「显示完整图谱」。页面提示范围过大，保留局部图谱并允许继续搜索类别。

## 验证

实现前，密集图谱用例复现了超过 3.5 秒的点击超时，标签测试也检出移动时重新计算偏移。
实现后，图谱浏览器 15 项通过并与 15 份完成证据逐项一致，设计页与行为树入口各 3 项通过，相关单元与规约检查 166 项通过。
验证环境为 Linux、Python 3.10、Playwright 1.62、Chrome 156 和 Node 24。验证范围为本地相关回归，未运行仓库全量回归或远端 CI。

## 复现

在[项目测试环境](../../README.md#测试)中运行，并确保 Node 可从 `PATH` 调用。

```sh
python -m pytest -q tests/unit/test_interactive_ontology_graph_ui.py \
  tests/unit/test_interactive_ontology_graph_http.py \
  tests/unit/test_interactive_ontology_graph_network.py \
  tests/unit/test_interactive_ontology_graph_projection.py \
  tests/unit/test_graph_browser_evidence.py tests/meta
mkdir -p .check-results
report_dir=$(mktemp -d .check-results/graph-layout-response-XXXXXX)
python tests/frontend/robotics-interactive-ontology-graph-browser.py --output "$report_dir/graph"
DATAMIND_ROBOTICS_DESIGN_TEST_OUTPUT="$report_dir/design" python -m pytest -q tests/frontend/robotics-design-browser.py \
  -k 'graph_warning_preserves_saved_revision or design_candidate_library_graph or new_build_completion_opens_exact_fixed_graph'
DATAMIND_ROBOTICS_TASK_TEST_OUTPUT="$report_dir/task" python -m pytest -q tests/frontend/robotics-task-browser.py \
  -k 'interactive_graph_task_keeps_fixed_version_mode or graph_starts_task_with_selected_ontology_version'
```

浏览器用例对 3 个类别、601 条连线的参数将 Chromium CPU 模拟降速设为 2 倍。套件生成逐项 JUnit 结果及对应浏览器证据。
