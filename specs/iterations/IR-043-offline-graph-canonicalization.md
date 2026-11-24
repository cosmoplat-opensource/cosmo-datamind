# IR-043 · 离线固定版本图谱的读取与规模检查

状态：delivered。关联 [DR-182](../decisions/DR-182-offline-graph-canonicalization.md) 和 [GRAPH-OFFLINE-READ-01](../acceptance.json)。

## 验收

- [x] 正常离线图谱返回所选版本的摘要，且一次请求只在共享 RDF 锁外规范化一次。
- [x] 三元组或带类型的命名主体数超过输入上限时，返回 HTTP 409，规范化调用次数为零。
- [x] 载荷、来源快照、原构建工件或发布报告损坏时，图谱拒绝读取。完整 JSON 导出保留原有事实、来源和报告。

本次实现的定向回归通过 111 个单元与接口用例、6 个浏览器用例。

## 复现

在[项目测试环境](../../README.md#测试)中运行：

```sh
python -m pytest -q tests/unit/test_robotics_offline_export.py tests/unit/test_interactive_ontology_graph_http.py tests/unit/test_interactive_ontology_graph_projection.py
python -m pytest -q tests/unit/test_ontology_detail_http.py tests/unit/test_robotics_design_export.py tests/unit/test_robotics_application_artifacts.py
python -m pytest -q tests/frontend/robotics-task-browser.py::test_interactive_graph_task_keeps_fixed_version_mode
python -m pytest -q tests/frontend/robotics-interactive-ontology-graph-browser.py::test_recycle_bin_graph_round_trip_preserves_fixed_version
python -m pytest -q tests/frontend/ontology-detail-browser.py::test_browser_downloads_original_fixed_json_and_owl_instead_of_latest
```

规模用例对同一份合成离线本体分别调低两项输入上限，记录 HTTP 请求中的规范化调用次数和持锁状态。该检查验证执行顺序，不测量耗时或并发吞吐量。
