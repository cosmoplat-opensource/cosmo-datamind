# IR-004 · 数据连接 + 数据可视化模块

- **状态**: delivered
- **关联**: [[DR-008-datasource-connection-model]]、[[DR-001-local-readonly-execution]]、[[DR-005-frontend-design-system]];对齐平台『配置数据源』『数据看板/大屏』

## 目标 / Goal
参照 `iip.iiot-platform.com/bigdata` 的「配置数据源·连接原始数据库」与「数据看板/数据大屏」,新增数据连接与数据可视化两模块。

## 交付 / Deliverables
- [x] 数据连接(数据资产组):连接列表(内置库 + SQLite 文件实时校验 + 外部库 MySQL/Doris/Hive/PG 登记连接串,按 host/port/db 自动生成 DSN);点就绪连接浏览表清单(行/列数)、预览前 100 行。
- [x] 数据可视化(顶层):选就绪源 → 只读 SQL 取数(快捷选表/指标自动填 SQL)→ ECharts 出图(折线/柱/面积/饼/表格/指标卡,自动 X/Y 映射)→ 拼装/保存多图数据看板。

## 任务 / Tasks
1. `/api/build/connect`·`connect/delete`(SQLite 校验/外部登记)、`/api/conn/tables`·`preview`、`_resolve_src`。
2. `/api/viz/run`(源感知只读查询)·`boards`·`save`·`delete`。
3. 前端 `cn*`(连接页)、`vz*`(可视化页,含代际守卫防串卡、实例 dispose 防泄漏)。

## 验收 / Acceptance
- 实测:连接列 3 源(示例 就绪/uploads 空/外部需驱动);浏览 108 表、预览真实行;DSN 自动生成 `jdbc:hive2://…`;非法表名→400。
- 可视化:取数 17×3 自动映射 X/Y 出图;多图入看板并保存/加载/删除;写查询被拒;连续重渲染无串卡/泄漏。
- `test_all.py` 数据连接/可视化断言通过(含只读拒写、非法 key→400)。
