# IR-044 · 从首次超限的图谱选择类别

状态：delivered。关联 [DR-178](../decisions/DR-178-initial-graph-class-scope.md) 和 [GRAPH-INITIAL-SCOPE-01](../acceptance.json)。

## 验收

- [x] 首次超过节点或连线上限时，显示范围提示和当前版本的完整对象索引，画布计数为零。
- [x] 选择或搜索类别可以显示符合上限的局部图谱，详情和计数与画布一致。尝试打开超限的类别邻域或恢复超限的完整图谱后仍可切换类别。
- [x] 超限的新修订不能覆盖原先展示的修订、选择、索引和画布。

本次定向回归通过 48 个组件与交互用例、15 个图谱浏览器用例和 2 个从固定版本图谱进入任务的浏览器用例。

## 复现

在[项目测试环境](../../README.md#测试)中运行：

```sh
python -m pytest -q tests/unit/test_interactive_ontology_graph_ui.py tests/unit/test_interactive_ontology_graph_network.py tests/unit/test_robotics_design_ui.py
python -m pytest -q tests/frontend/robotics-interactive-ontology-graph-browser.py
python -m pytest -q tests/frontend/robotics-task-browser.py::test_interactive_graph_task_keeps_fixed_version_mode
```
