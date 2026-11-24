# DR-165 · 机器人交互式本体图谱命名与视图契约

状态：accepted。日期：2026-11-19。依据：维护者要求使用“交互式本体图谱”描述当前功能，现阶段仅适用于机器人本体。设计决定与实现交付状态分开记录。

实现交付状态：delivered。

## 决策

中文名称统一为“交互式本体图谱”，页面标题明确为“机器人交互式本体图谱”。前端模块、样式、测试及文档使用 `interactive-ontology-graph` / `interactive_ontology_graph`，后端只读投影模块为 `ontology_assets.interactive_ontology_graph_view`，控制器为 `InteractiveOntologyGraphController`。

仅机器人设计会话和机器人固定版本浏览接入这套组件。固定版本入口保留服务端机器人领域检查，数据本体不能通过该入口取得交互式图谱。数据本体的主图、构建预览、问数高亮、证据、详情与评审维持现有组件和服务，不安排后续自动接入。

机器人设计会话及历史修订响应通过 `interactive_graph_view` 返回只读投影；固定版本入口使用 `/api/ontology-assets/<ontology_id>/versions/<version_id>/views/interactive-ontology-graph`，页面参数为 `view=interactive-ontology-graph`；视图 schema 为 `datamind-interactive-ontology-graph/1`。这些名称只作用于展示层，不修改机器人本体 RDF、已存设计、资产版本、来源摘要和执行资格。

## 调用方迁移

设计操作的成功回执可包含图谱显示提示。若 `interactive_graph_view` 为 `null`，请显示 `interactive_graph_warning.message`，并按回执中的修订、阶段和运行编号更新设计状态。图谱提示无需重新提交设计操作。见 [DR-172](DR-172-design-operation-receipt.md)。

本体移到回收站后，从“查看固定版本”进入详情，再点“打开图谱”可浏览历史版本。外部调用方读取该版本的图谱时须附加 `?state=deleted`。普通读取返回 HTTP 409（`ontology_deleted`），恢复本体后原图谱链接可再次使用。详见 [DR-171](DR-171-fixed-version-graph-lifecycle.md)。

机器人前后端和测试调用方同步使用新名称。部署升级后刷新页面，并从机器人本体库重新打开图谱以取得当前深链；外部调用方按上述路径、字段和 schema 更新。已有 `view=relations` 入口继续保留。数据本体入口不迁移，已有本体和数据库无需重写，也不重新生成用户资产。

## 验证

GRAPH-NAME-01 映射模块导入、schema、固定版本 HTTP、设计与历史修订只读响应、完整数据本体版本的拒绝路径，以及实际浏览器交互测试。已有图谱语义、隔离、错误路径、覆盖率及布局断言保留，测试入口见 `specs/acceptance.json`。

2026-11-19 的命名修订对应开发快照 `88380ef`。该修订的定向回归覆盖 165 项单元、HTTP、组件和规约检查，另有图谱浏览器 9 项、设计浏览器 29 项通过。相关 lint 和类型检查通过，无跳过。该次未运行全量回归或全仓覆盖率门禁。原图谱交付的验证范围及仓库复现命令见 [IR-031](../iterations/IR-031-robotics-interactive-ontology-graph-visualization.md)。
