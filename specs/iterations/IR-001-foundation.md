# IR-001 · DataMind 基座(数据资产/建模/指标/治理/开发)

- **状态**: delivered
- **关联**: [[DR-001-local-readonly-execution]]、[[DR-005-frontend-design-system]];平台 DR-001(元模型 IR)

## 目标 / Goal
把平台 engine 的产物(示例 数据本体 IR、应用本体 IR、107 指标、术语)产品化为可浏览、可查、可视化的前端基座,
本地只读执行,断网可用,观感对齐 iiot-platform。

## 交付 / Deliverables
- [x] 总览驾驶舱:KPI + 收入/毛利/产量全量趋势(`/api/overview`)。
- [x] 数据目录:108 表清单/字段(含中文)/预览(`/api/tables`·`/api/table/<n>`)。
- [x] 本体图谱:G6 四布局 + 对象/事件/资产/角色四色 + verb 边 + 多图谱切换 + 节点/关系证据卡 + 三格式导出(`/api/graphs`·`/api/graph/<k>`)。
- [x] 建模工作台:G6 画布 + 12 白名单编辑算子 + 撤销 + 存版本 + 锻造 + 重建(`/api/ont/apply·undo·save·forge·rebuild`)。
- [x] 数仓分层 / 指标中心(107 指标 + 血缘,派生/复合展示业务口径)/ 构成规则 / 数据质量(扫描基数 + 预警)/ SQL 工作台(只读)。
- [x] 术语管理(655 条)、SPARQL 查询(rdflib)、本体库、成果库、API 目录、作业中心、系统管理、平台原版 iframe。

## 任务 / Tasks
1. Flask `server.py` 骨架 + 本地只读 `q()` + IR 加载(demo/app/cq/forged/built)。
2. 单文件 SPA `ui/index.html` + 品类分组导航 + ECharts/G6 本地内置。
3. 各页面端点与渲染;字段中文化(`translate_cn.py`,876/876 字段)。

## 验收 / Acceptance
- `test_all.py` 覆盖各端点 happy path + 边界(200/404/400)。
- 计数与真数一致:108 表 / 186,833 行 / 107 指标 / 655 术语(实测核对)。
- 断网可用(vendor 本地内置);控制台零错误。
