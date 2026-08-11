# IR-011 · 单体路由蓝图化(逐簇拆分)

- **状态 / Status**: in-progress
- **日期 / Date**: 2026-07-31
- **关联 / Refs**: [[DR-043-blueprint-modularization]]、[[DR-044-json-store-abstraction]];实证=`srv_context.py`、`server.py`、集成套件 526/531

## 目标 Goal

按 DR-043 分步方案,把 `server.py`(5000+ 行/121 路由)逐簇拆成 Flask blueprint,
**每步集成套件(535 断言)回归验证**,绿了再拆下一簇。先建共享上下文基座消除 blueprint↔server 循环导入。

## 交付 Deliverables

- [x] **共享上下文基座 `srv_context.py`**:抽 server 里无路由/无 app 依赖的基础原语
  (`ro_connect`/`sql_is_readonly`/`_WRITE_LOCK`/`_atomic_json`/`_atomic_text`),供 server 与各 blueprint 共用。
  server 改为 import,删本地重复定义;集成 526/531 保持,pyflakes 零告警,test_all Z21 白名单补 srv_context。
- [x] **engine 共享层 `srv_engine.py`**:运行时缓存(`_RT_CACHE`/`runtime_cached`/`_drv_order`)+
  引擎配置层(常量/读写/应用/掩码)+ 引擎回复语义(`_looks_like_error`)。
  coupling 分析证实必须先抽:`_load_engine_cfg` 被 deepqa 按任务选模与启动自举调用、`LLM_ENV` 17 处,
  直接搬进 blueprint 会循环导入。`_ENV_LOCKED_AT_BOOT` 改为模块 import 时快照,时序不变量保持。
- [x] **首个 blueprint `bp_engine.py`(engine 簇,5 路由)**:`/api/engine/config`(GET/POST)、
  `/api/engine/llm/test`、`/api/engine/llm/models`、`/api/engine/test` 迁出;server 注册 blueprint。
  app 级 `before_request` CSRF 守卫对 blueprint 同样生效,**安全模型不变**。
  路由总数 121 不变(server 116 + bp_engine 5);集成 526/531 与迁移前一致。
- [ ] 其余五簇同理:每簇先识别其跨切面共享层(IR/图谱访问、config),抽为共享模块,再迁路由。每步集成验证。

## 任务 Tasks(每项一次提交)

1. 建 srv_context 基座,server import 之(基础原语零风险抽取);集成验证。
2. 抽 engine 簇为 bp_engine + 注册;集成验证。
3. …逐簇推进。

## 验收 Acceptance

- [x] srv_context 抽取:单元 106 绿、集成 526/531(与抽取前一致)、pyflakes 零告警、chk 仍 535。
- [ ] 每簇 blueprint 迁移后集成套件不劣化(以 526/531 为基线)。

## 备注

- **规范**:一次一簇,拆完立刻跑 535 断言;任一簇迁移致集成劣化即回滚该簇,不叠加。
- srv_context 只收基础原语;路径(DB/WORK)与引擎 sys.path 自举仍留 server(耦合装配),按需再迁。
- 已抽出的纯模块(dao_core/store/eval 等)本可堆在 server,现独立可测——降耦合为逐簇拆铺路(DR-043)。
