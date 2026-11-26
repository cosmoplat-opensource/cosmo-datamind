# IR-057 · 保存与导出时的 RDF/XML 表示限制

状态：delivered。关联 [DR-196](../decisions/DR-196-graph-save-format-errors.md) 和[验收映射](../acceptance.json)中的 GRAPH-FORMAT-01。

## 验收

- [x] 副本使用 RDF/XML 无法表示的属性 IRI 时，保存返回 422，提示说明保存原因并列出该 IRI，不建议更换导出格式，版本列表不变。
- [x] 同一副本的 OWL 导出返回 422，列出该 IRI 并建议改用 Turtle 或 JSON-LD。Turtle、JSON-LD 导出和编辑视图正常返回。
- [x] 提示最多列出 5 个 IRI，超出时给出总数。

## 定向验证

使用开发测试依赖、SQLite 隔离存储和合成本体，在仓库根目录使用 Bash 复现。

```bash
python -m pytest tests/unit/test_graph_save_format_errors.py tests/unit/test_graph_edit_copy.py \
  tests/unit/test_graph_copy_version.py tests/unit/test_graph_copy_boundaries.py \
  tests/unit/test_interactive_ontology_graph_http.py
python -m pytest tests/meta/test_acceptance_traceability.py tests/meta/test_doc_consistency.py \
  tests/meta/test_repository_materials.py tests/meta/test_public_content.py
```
