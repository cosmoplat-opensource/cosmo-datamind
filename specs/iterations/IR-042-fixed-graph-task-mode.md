# IR-042 · 固定版本图谱的任务模式

状态：delivered。关联 [DR-176](../decisions/DR-176-fixed-graph-task-mode.md) 和 [GRAPH-TASK-MODE-01](../acceptance.json)。

## 验收

- [x] 打包演示版本返回 `demo`，普通版本返回 `real`。缺少模式元数据的旧演示版本仍可从固定 RDF 识别。
- [x] 从活动图谱点击“基于此本体生成行为树”后，应用检查报告的模式、本体和版本与所选图谱一致。回收站历史查看延续只读约束。

浏览器用例使用打包演示本体和隔离构建的普通本体，核对图谱接口返回值、实际发出的应用检查请求以及服务端返回的报告。验证范围到应用检查为止。

## 复现

在[项目测试环境](../../README.md#测试)中运行：

```sh
python -m pytest -q tests/unit/test_interactive_ontology_graph_projection.py tests/unit/test_interactive_ontology_graph_http.py
python -m pytest -q tests/frontend/robotics-task-browser.py -k interactive_graph_task_keeps_fixed_version_mode
```
