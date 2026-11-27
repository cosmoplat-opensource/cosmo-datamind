# IR-053 · 交互式图谱空白查找不选中对象

状态：delivered。关联 [DR-192](../decisions/DR-192-graph-blank-search.md) 和[验收映射](../acceptance.json)中的 GRAPH-SEARCH-01。

## 验收

- [x] 查找框为空或只有空格时点击“查找”，显示输入提示，焦点回到查找框，当前详情和展示范围不变。
- [x] 首尾带空格的查找词按去掉空格后的名称匹配。
- [x] 原有的关系与属性查找仍显示定义和作用对象。

## 定向验证

使用开发测试依赖、SQLite 隔离存储、合成图谱和 Chromium，在仓库根目录用 Bash 复现。

```bash
python -m pytest tests/frontend/robotics-interactive-ontology-graph-browser.py
python -m pytest tests/unit/test_interactive_ontology_graph_ui.py tests/meta/test_acceptance_traceability.py \
  tests/meta/test_doc_consistency.py tests/meta/test_repository_materials.py tests/meta/test_public_content.py
```
