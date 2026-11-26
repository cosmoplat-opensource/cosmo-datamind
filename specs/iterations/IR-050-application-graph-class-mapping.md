# IR-050 · 应用图中的匹配类别

状态：delivered。关联 [DR-189](../decisions/DR-189-application-graph-class-mapping.md) 和[验收映射](../acceptance.json)中的 GRAPH-CLASS-01。

## 目标与验收

- [x] 在固定应用版本和编辑副本中，将已确认匹配的设计类别与应用类别展示为一个节点和一个类别选项。节点详情可追溯两端 IRI。
- [x] 在同一蓝圈下展示两端的实例与关系，对双重归属的实例计数一次。仅名称相同、缺少目标类别声明或映射尚未确认时分别展示。
- [x] 从合并后的蓝圈新增实例，使用应用类别 IRI 写入归属。逐条解除已有归属或类别关系时保留其他原始断言，导出保留两个原始类别。

## 验证与复现

合成 RDF 样本包含一个已匹配的类别对、一个独立同名类、分别归属两端的实例和一个双重归属实例。浏览器用例从完成应用适配的固定版本进入编辑副本，检查节点和表单选项、创建实例，并回读下载的 OWL。

本轮使用仓库的 Python 开发依赖、Playwright 和 Chromium。投影、编辑、HTTP、网络、组件和浏览器证据检查通过 87 项，应用工作流通过 12 项，验收映射和仓库材料检查通过 45 项。图谱浏览器套件的 19 项用例均通过，逐例运行记录与 JUnit 一致。其中适配用例从一条合并显示的继承连线解除设计侧断言，再回读导出文件确认应用侧断言仍然存在。

在仓库根目录运行以下定向回归。全工程回归不在本次验证范围内。

```sh
python -m pytest tests/unit/test_mapped_graph_classes.py tests/unit/test_graph_edit_copy.py \
  tests/unit/test_interactive_ontology_graph_{http,projection,ui,network}.py tests/unit/test_graph_browser_evidence.py
python tests/frontend/robotics-interactive-ontology-graph-browser.py
python -m pytest tests/unit/test_robotics_application_workflow.py
python -m pytest tests/meta/test_acceptance_traceability.py tests/meta/test_repository_materials.py
```
