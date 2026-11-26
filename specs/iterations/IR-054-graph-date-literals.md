# IR-054 · 图谱编辑副本的日期与日期时间数据值

状态：delivered。关联 [DR-193](../decisions/DR-193-graph-date-literals.md) 和[验收映射](../acceptance.json)中的 GRAPH-LITERAL-01。

## 验收

- [x] 合法的日期和日期时间写法均可写入，包括时区、小数秒和闰日。
- [x] 只写到小时或分钟、带两个时区、紧凑写法、周日期、不存在的日期、越界的时分秒或时区、非 ASCII 数字、超出 0001 至 9999 年和 `24:00:00` 的值均被拒绝，提示正确写法，副本不变。
- [x] 固定种子的性质测试在 0001 至 9999 年间随机生成日期、时间、小数秒和时区，合法写法都通过，单处结构破坏都被拒绝。

## 定向验证

使用开发测试依赖和合成 RDF，在仓库根目录使用 Bash 复现。

```bash
python -m pytest tests/unit/test_graph_literal_lexical.py \
  tests/unit/test_properties.py::test_graph_edit_date_values_match_calendar_and_xsd_shape \
  tests/unit/test_graph_{edit_copy,directional_relations,relation_endpoints,copy_version}.py
python -m pytest tests/meta/test_acceptance_traceability.py tests/meta/test_doc_consistency.py \
  tests/meta/test_repository_materials.py tests/meta/test_public_content.py
```
