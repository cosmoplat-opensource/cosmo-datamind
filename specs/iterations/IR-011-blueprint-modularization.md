# IR-011 · 单体路由蓝图化(逐簇拆分)

- **状态 / Status**: in-progress
- **日期 / Date**: 2026-07-31
- **关联 / Refs**: [[DR-043-blueprint-modularization]]、[[DR-044-json-store-abstraction]];实证=`srv_context.py`、`server.py`、集成套件 526/531

## 目标 Goal

按 DR-043 分步方案,把 `server.py`(5000+ 行/121 路由)逐簇拆成 Flask blueprint,
**每步集成套件(535 断言)护航**,绿了再拆下一簇。先建共享上下文基座消除 blueprint↔server 循环导入。

## 交付 Deliverables

- [x] **共享上下文基座 `srv_context.py`**:抽 server 里无路由/无 app 依赖的基础原语
  (`ro_connect`/`sql_is_readonly`/`_WRITE_LOCK`/`_atomic_json`/`_atomic_text`),供 server 与各 blueprint 共用。
  server 改为 import,删本地重复定义;集成 526/531 保持,pyflakes 零告警,test_all Z21 白名单补 srv_context。
- [ ] **首个 blueprint(engine 簇)—— 前置未满足,待解耦**:coupling 分析(2026-07-31)发现
  `/api/engine/*` 的配置层**跨簇共享**,不能直接搬进 blueprint:
  `_load_engine_cfg` 被 line 776(deepqa 按任务选模 `_task_model`)与 line 3033(启动即应用)调用;
  `LLM_ENV` 17 处、`_mask_key`/`ENGINE_TASKS` 亦跨簇。直接搬会造成 blueprint↔server 循环导入。
  **正解**:先把 engine 配置层(常量 + `_load/_save/_apply_engine_cfg` + `LLM_ENV` + `_mask_key`)
  抽为**共享配置模块**,engine 路由与 line 776 调用方都从它 import,之后 engine 路由才可迁 blueprint。
  该抽取的前置又是路径(`WORK`)入 srv_context(`ENGINE_CFG_F=WORK/engine_config.json`)。
- [ ] 其余五簇同理:每簇先识别其跨切面共享层(IR/图谱访问、config),抽为共享模块,再迁路由。每步集成验证。

## 任务 Tasks(每项一次提交)

1. 建 srv_context 基座,server import 之(基础原语零风险抽取);集成验证。
2. 抽 engine 簇为 bp_engine + 注册;集成验证。
3. …逐簇推进。

## 验收 Acceptance

- [x] srv_context 抽取:单元 106 绿、集成 526/531(与抽取前一致)、pyflakes 零告警、chk 仍 535。
- [ ] 每簇 blueprint 迁移后集成套件不劣化(以 526/531 为基线)。

## 备注

- **纪律**:一次一簇,拆完立刻跑 535 断言;任一簇迁移致集成劣化即回滚该簇,不叠加。
- srv_context 只收基础原语;路径(DB/WORK)与引擎 sys.path 自举仍留 server(耦合装配),按需再迁。
- 已抽出的纯模块(dao_core/store/eval 等)本可堆在 server,现独立可测——降耦合为逐簇拆铺路(DR-043)。
