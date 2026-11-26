# IR-058 · 图谱编辑副本的 RDF 库版本边界

状态：delivered。关联 [DR-197](../decisions/DR-197-graph-edit-rdflib-support.md) 和[验收映射](../acceptance.json)中的 GRAPH-RDFLIB-01。

## 验收

- [x] 运行时 `rdflib` 约束为 CI 固定版本起、下一个主版本前，当前安装的主版本与之相符。
- [x] 原文保留失效时，编辑预览和保存返回 503 `graph_edit_unavailable`，版本列表不变，只读图谱仍返回 200。恢复后编辑副本照常可用。
- [x] 已有的原文保留、导出和精确删除数据值用例继续通过。

## 定向验证

使用开发测试依赖（RDFLib 7.6.0）、SQLite 隔离存储和合成本体，在仓库根目录使用 Bash 复现。失效场景用测试替身把处理器换回 RDFLib 默认实现来模拟。

```bash
python -m pytest tests/unit/test_graph_lexical_support.py tests/unit/test_graph_edit_copy.py \
  tests/unit/test_graph_{copy_version,copy_boundaries,save_format_errors,literal_lexical,unlink_declarations}.py \
  tests/unit/test_mapped_graph_classes.py tests/unit/test_interactive_ontology_graph_http.py
python -m pytest tests/meta/test_acceptance_traceability.py tests/meta/test_doc_consistency.py \
  tests/meta/test_repository_materials.py tests/meta/test_public_content.py
```
