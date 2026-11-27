# IR-041 · 图谱模型源码的文件结尾

状态：delivered。关联 [DR-180](../decisions/DR-180-graph-model-file-ending.md) 和 [GRAPH-SOURCE-01](../acceptance.json)。

## 验收

- [x] [图谱模型源码](../../ui/modules/interactive-ontology-graph/model.js)的最后一行以换行符结束，文件末尾没有额外空行。
- [x] 通过图谱控制器分页展开实例时，共享实例仍能正确去重。

## 复现

在[项目测试环境](../../README.md#测试)中运行以下命令，分别检查源码结尾和图谱控制器行为。

```sh
python -m pytest -q tests/meta/test_repository_materials.py::test_graph_model_ends_with_one_line_terminator
python -m pytest -q tests/unit/test_interactive_ontology_graph_ui.py
```
