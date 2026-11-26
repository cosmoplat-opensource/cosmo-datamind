# IR-055 · 图谱编辑副本沿用显示名称

状态：delivered。关联 [DR-194](../decisions/DR-194-graph-edit-display-labels.md) 和[验收映射](../acceptance.json)中的 GRAPH-LABEL-01。

## 验收

- [x] 含旧版配置名称的固定版本在只读浏览和编辑副本中都显示统一名称，节点列表和详情标题不再出现旧名称。
- [x] 「编辑实体」以本体中保存的原文预填中文名称，未修改即应用时提示没有可应用的改动，显示名称不会写回本体。

## 定向验证

使用开发测试依赖、SQLite 隔离存储、合成本体和 Chromium，在仓库根目录使用 Bash 复现。浏览器用例先经保存接口生成含旧版名称实例的候选版本，再打开其图谱进入编辑副本。

```bash
python -m pytest tests/frontend/robotics-interactive-ontology-graph-browser.py
python -m pytest tests/unit/test_interactive_ontology_graph_ui.py tests/meta/test_acceptance_traceability.py \
  tests/meta/test_doc_consistency.py tests/meta/test_repository_materials.py tests/meta/test_public_content.py
```
