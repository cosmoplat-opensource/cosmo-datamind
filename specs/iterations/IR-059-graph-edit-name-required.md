# IR-059 · 编辑实体时中文名称必填

状态：delivered。关联 [DR-198](../decisions/DR-198-graph-edit-name-required.md) 和[验收映射](../acceptance.json)中的 GRAPH-LABEL-02。

## 验收

- [x] 「编辑实体」清空中文名称后点击「应用」，对话框保持打开，不发送编辑请求；填写名称后可以正常应用。
- [x] 服务端拒绝空名称和只含空白的名称，改不改 IRI 都一样，副本不变。名称有值时仍可清空说明。

## 定向验证

使用开发测试依赖、SQLite 隔离存储、合成本体和 Chromium，在仓库根目录使用 Bash 复现。

```bash
python -m pytest tests/unit/test_graph_edit_name_required.py tests/unit/test_graph_edit_copy.py \
  tests/unit/test_interactive_ontology_graph_ui.py
python -m pytest tests/frontend/robotics-interactive-ontology-graph-browser.py
python -m pytest tests/meta/test_acceptance_traceability.py tests/meta/test_doc_consistency.py \
  tests/meta/test_repository_materials.py tests/meta/test_public_content.py
```
