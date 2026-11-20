# DR-165 · 机器人交互式本体图谱命名与视图契约

状态：accepted。日期：2026-11-19。依据：维护者要求使用“交互式本体图谱”描述当前功能，现阶段仅适用于机器人本体。设计决定与实现交付状态分开记录。

## 决策

中文名称统一为“交互式本体图谱”，页面标题明确为“机器人交互式本体图谱”。前端模块、样式、测试及文档使用 `interactive-ontology-graph` / `interactive_ontology_graph`，后端只读投影模块为 `ontology_assets.interactive_ontology_graph_view`，控制器为 `InteractiveOntologyGraphController`。

仅机器人设计会话和机器人固定版本浏览接入这套组件。固定版本入口保留服务端机器人领域检查，数据本体不能通过该入口取得交互式图谱。数据本体的主图、构建预览、问数高亮、证据、详情与评审维持现有组件和服务，不安排后续自动接入。

机器人设计会话及历史修订响应通过 `interactive_graph_view` 返回只读投影；固定版本入口使用 `/api/ontology-assets/<ontology_id>/versions/<version_id>/views/interactive-ontology-graph`，页面参数为 `view=interactive-ontology-graph`；视图 schema 为 `datamind-interactive-ontology-graph/1`。这些名称只作用于展示层，不修改机器人本体 RDF、已存设计、资产版本、来源摘要和执行资格。

## 调用方迁移

机器人前后端和测试调用方同步使用新名称。部署升级后刷新页面，并从机器人本体库重新打开图谱以取得当前深链；外部调用方按上述路径、字段和 schema 更新。已有 `view=relations` 入口继续保留。数据本体入口不迁移，已有本体和数据库无需重写，也不重新生成用户资产。

## 验证

GRAPH-NAME-01 映射模块导入、schema、固定版本 HTTP、设计与历史修订只读响应、完整数据本体版本的拒绝路径，以及实际浏览器交互测试。已有图谱语义、隔离、错误路径、覆盖率及布局断言保留，测试入口见 `specs/acceptance.json`。

当前修订的实现交付状态：delivered。本次相关回归通过：165 项单元、HTTP、组件及规约检查，9 项图谱浏览器用例，29 项机器人设计浏览器用例；相关文件 lint、类型检查通过，无跳过。未完成当前修订的全量回归及全仓覆盖率门禁，不据此声明全仓通过。以上为历史阶段结果；当前检出版本的依赖环境及完整复现入口见[仓库验证说明](../../README.md#测试)，具体相关测试见 `specs/acceptance.json` 的 GRAPH-NAME-01 映射。
