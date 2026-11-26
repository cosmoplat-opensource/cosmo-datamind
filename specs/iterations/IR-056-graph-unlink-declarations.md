# IR-056 · 解除断言时的属性身份声明

状态：delivered。关联 [DR-195](../decisions/DR-195-graph-unlink-declarations.md) 和[验收映射](../acceptance.json)中的 GRAPH-UNLINK-01。

## 验收

- [x] 未声明类型的对象属性或数据属性仍有其他使用时，解除一条关系或删除一个数据值只移除该断言，不新增声明。
- [x] 移除最后一处使用时补上对应的对象属性或数据属性声明。已声明的属性不重复声明。

## 定向验证

使用开发测试依赖和合成 RDF，在仓库根目录使用 Bash 复现。

```bash
python -m pytest tests/unit/test_graph_unlink_declarations.py tests/unit/test_graph_edit_copy.py \
  tests/unit/test_graph_{directional_relations,relation_endpoints,literal_lexical}.py \
  tests/unit/test_mapped_graph_classes.py tests/unit/test_properties.py
python -m pytest tests/meta/test_acceptance_traceability.py tests/meta/test_doc_consistency.py \
  tests/meta/test_repository_materials.py tests/meta/test_public_content.py
```
