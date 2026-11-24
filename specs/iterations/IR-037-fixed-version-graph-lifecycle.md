# IR-037 · 固定版本图谱的历史读取

状态：delivered。关联 [DR-171](../decisions/DR-171-fixed-version-graph-lifecycle.md) 和 GRAPH-LIFE-01。

## 交付与验收

- 普通图谱读取遵循本体删除状态，恢复后可再次读取同一固定版本。
- 显式历史请求保留版本和来源校验，浏览前后的资产记录与文件一致。
- 从回收站打开固定版本详情、进入图谱、返回详情，全程保留同一本体、版本和历史状态。

## 复现

在[项目测试环境](../../README.md#测试)中执行以下相关回归。浏览器用例使用合成资产和临时服务。

```sh
python -m pytest -q -n 2 --dist=loadfile tests/unit/test_interactive_ontology_graph*.py \
  tests/unit/test_robotics_graph_views.py tests/unit/test_robotics_http_review_fixes.py \
  tests/unit/test_ontology_detail_http.py tests/unit/test_ontology_detail_ui.py \
  tests/unit/test_asset_lifecycle.py tests/unit/test_asset_lifecycle_http.py \
  tests/meta/test_acceptance_traceability.py
python -m pytest -q tests/frontend/robotics-interactive-ontology-graph-browser.py
python -m pytest -q tests/frontend/robotics-build-browser.py \
  -k 'fixed_robotics_relationship_view or view_zoom or old_tree_link or legacy_configuration_names'
```
