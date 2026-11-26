# IR-051 · 图谱关系端点与数据值校验

状态：delivered。关联 [DR-190](../decisions/DR-190-graph-relation-endpoints.md) 和[验收映射](../acceptance.json)中的 GRAPH-ENDPOINT-01 和 GRAPH-DIRECTION-01。

## 验收

- [x] 新增实例关系时，对任一所选对象属性，起点列表只列出满足其定义域的实例，终点列表只列出满足其值域的实例。切换属性后重新筛选两端，任一端无候选时提示原因并禁用「应用」。
- [x] 满足所选属性两端声明的连接可写入并导出。起点或终点不符合对应声明时，编辑和导出接口均返回 422，并指出不符合声明的一端。已有连接即使不符合当前声明也可解除。
- [x] 对于未声明端点、未分类实例、命名子类和等价类、应用类别映射、多重声明和匿名并集，候选列表与服务端实际接受的组合一致。
- [x] 新增继承关系时排除自身和后代，服务端拒绝直接或间接循环，历史环中的边可解除。等价和互斥保留对称语义。
- [x] 实例归属、定义域与值域分别提供适用端点。数据属性的值域允许已声明的数据类型，接口拒绝类别和虚构的数据类型。
- [x] 新增数据值同时校验实例类别、字面值类型及已有的词法规则。服务端在导出前重放校验。旧值仍可移除。

## 定向验证

浏览器用例切换定义域和值域不同的对象属性，核对起点与终点列表、无候选时的提示和提交按钮，并提交有效连接。接口用例分别使用预定义属性和副本中新建的属性，核对两端不匹配时的错误，并从导出的 RDF 中读回有效连接。另有一致性用例将真实编辑投影交给前端筛选函数，并将样本中所有实例端点组合逐个交由服务端校验。

使用隔离的合成图、固定版本接口和 Chromium。浏览器用例核对继承候选、切换属性后的值域候选，在同一副本中设置数据属性的定义域，再分别从符合与不符合该定义域的实例打开表单。

两组功能首次验收时，图谱与接口定向检查通过 89 项，真实浏览器用例通过 21 项，验收追踪与仓库材料检查通过 45 项。浏览器逐例记录与 JUnit 结果一致。JSON-LD 回读出现一条 RDFLib 弃用提示。

将同一对实例分别用于两项端点声明相反的对象属性后，实例关系端点用例通过 4 项，浏览器属性切换用例通过 1 项，验收追踪与仓库材料检查通过 45 项。可在仓库根目录复现这 50 项检查。

```bash
python -m pytest tests/unit/test_graph_relation_endpoints.py \
  tests/meta/test_acceptance_traceability.py tests/meta/test_repository_materials.py
python -m pytest tests/frontend/robotics-interactive-ontology-graph-browser.py::test_object_property_filters_both_instance_selectors
```

在仓库根目录安装开发和浏览器测试依赖后可运行以下检查。本次验证范围为图谱编辑及其接口。

```bash
python -m pytest tests/unit/test_graph_directional_relations.py tests/unit/test_graph_relation_endpoints.py \
  tests/unit/test_graph_edit_copy.py tests/unit/test_mapped_graph_classes.py \
  tests/unit/test_interactive_ontology_graph_{projection,http,ui,network}.py \
  tests/unit/test_properties.py::test_graph_edit_deletes_only_selected_named_individual_facts
python tests/frontend/robotics-interactive-ontology-graph-browser.py
python -m pytest tests/meta/test_acceptance_traceability.py tests/meta/test_repository_materials.py
```
