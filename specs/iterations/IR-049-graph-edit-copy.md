# IR-049 · 交互式图谱基础编辑

状态：delivered。关联 [DR-188](../decisions/DR-188-graph-edit-copy.md) 与[验收映射](../acceptance.json)中的 GRAPH-EDIT-01。

## 目标与交付

在活动机器人固定版本的画布中编辑副本，完成一次从创建实体、设置关系到导出本体的操作。复用原有画布、固定工件读取和 RDF 投影，提供[操作与接口说明](../../domains/robotics/README.md#在固定版本图谱编辑副本)。

## 验收

- [x] 在画布中新增类别、属性、实例与关系，修改实体 IRI、中文名称和说明。实例可添加数据值、归入类别或作为未分类实例选择。
- [x] 解除一条关系或数据值只影响选中的断言及其附属注解。预览实体删除覆盖图外事实，保留其他实体；不支持的共享结构和多角色 IRI 整体拒绝。
- [x] 取消预览及取消等待中的编辑保留原状态，撤销和重做恢复相应的显示与选择。OWL、Turtle 和 JSON-LD 导出可由 RDF 解析器读回。
- [x] 编辑前后的固定版资产记录和工件字节一致。版本摘要不匹配及回收站资产均不能进入编辑副本。

## 验证与复现

在隔离目录中使用合成本体，验证从固定版本打开编辑副本、修改 RDF 断言、撤销与重做，以及导出后回读的流程。测试环境为 Python 3.10.12、RDFLib 7.6.0、Playwright 1.62.0 和 Chromium 内核 156。

打开活动机器人本体的固定版本图谱，点击工具栏中的「编辑副本」即可新增实例。取消删除预览后，实例仍在图中；确认删除后，可通过「撤销」恢复实例。

删除用例还覆盖隐式属性、带嵌套注解的数据值、带前导零的类型值、共享限制和其他语言标签。另有浏览器用例验证，在编辑请求返回前按 Esc 取消，实例保留原名称。

基础版交付时，图谱、性质、HTTP 与验收映射检查通过 96 项，离线读取规模与规范化检查通过 3 项。图谱浏览器通过 17 项，运行记录与 JUnit 结果一致。RDFLib 的 JSON-LD 回读产生一条弃用提示。

在仓库根目录准备项目开发和浏览器测试依赖后运行以下定向检查。

```sh
python -m pytest tests/unit/test_graph_edit_copy.py tests/unit/test_properties.py::test_graph_edit_deletes_only_selected_named_individual_facts \
  tests/unit/test_interactive_ontology_graph_http.py tests/unit/test_interactive_ontology_graph_projection.py \
  tests/unit/test_interactive_ontology_graph_ui.py tests/unit/test_interactive_ontology_graph_network.py \
  tests/unit/test_robotics_graph_views.py tests/unit/test_graph_browser_evidence.py tests/meta/test_acceptance_traceability.py
python -m pytest tests/unit/test_robotics_offline_export.py -k offline_graph_checks_input_budget
python tests/frontend/robotics-interactive-ontology-graph-browser.py
python -m pytest tests/meta/test_repository_materials.py
ruff check bp_robotics_review.py ontology_assets/{graph_edit_copy,interactive_ontology_graph_view}.py \
  tests/unit/{test_graph_edit_copy,test_interactive_ontology_graph_ui,test_properties}.py \
  tests/frontend/robotics-interactive-ontology-graph-browser.py
mypy bp_robotics_review.py ontology_assets/{graph_edit_copy,interactive_ontology_graph_view}.py
```

验证范围为内存编辑、图谱读取、画布交互、导出与规约。全工程回归和真实设备执行不在本次验证范围内。

## 实例归属修正

选择「实例归属」后，起点列表包含刚创建的未分类实例，终点列表提供已有命名类别。
应用后导出的 OWL 包含实例指向该类别的 `rdf:type` 断言；解除该实例唯一的类别归属后，它重新出现在「未分类实例」分组。

本次定向回归通过 3 个编辑浏览器用例和 31 个编辑服务与图谱组件用例。浏览器用例同时核对标准关系选项的 RDF 标识，并从实际下载的 OWL 回读归属关系。

```sh
python -m pytest tests/frontend/robotics-interactive-ontology-graph-browser.py -k 'edit_copy or unclassified_individual_can'
python -m pytest tests/unit/test_graph_edit_copy.py tests/unit/test_interactive_ontology_graph_ui.py
```
