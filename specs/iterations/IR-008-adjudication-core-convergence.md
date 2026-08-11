# IR-008 · 裁决核收敛与算法强化

- **状态 / Status**: in-progress
- **日期 / Date**: 2026-07-31
- **关联 / Refs**: [[PROPOSAL-sdd-tdd-improvement]]、[[DR-035-unified-adjudication-core]];后续 DR-036(自引用键)/DR-037(多信号)/DR-038(自适应 θ)

## 目标 Goal

以 IR-007 的已知绿基线为起点,用红-绿把 `quick_build` 与上游 `relation_discovery`
两份**漂移裁决核**收敛为单一事实源,并为后续算法强化(自引用键/多信号/自适应 θ)铺好可测底座。

## 交付 Deliverables

- [x] `dao_core.py`:自包含、数据无关的单一裁决核(原语 + `classify` 三态决策 + 常量单一事实源)。
- [x] `tests/unit/test_dao_core.py`:20 条一致性测试,含
  - [x] 重叠公式精度(原始不四舍五入)
  - [x] 规范强门槛三态(verified/candidate/drop:含 MIN_DISTINCT、PK-作子键排除、非唯一父键)
  - [x] compat 模式复现 quick_build
  - [x] 与上游 `relation_discovery.name_score` **平价测试**(参数化 4 例 + overlap 取整一致)
- [x] `quick_build.py` 接入 dao_core(compat 模式):命名/重叠/决策全走单一核。
- [x] **零回归验证**:108 表 demo(imom_metrics.db)重构前后逐值一致(265 关系/104 verified,note+evidence 全量相同)。
- [x] **DR-036 自引用键**:放开 `pt==t` + 角色词典 + name_ok 角色路径;`test_quick_build` 自引用断言已反转为「能发现」;108 表 demo 新增 1 条真自引用边(0 既有改动);health_check 自反豁免。
- [x] **强门槛翻转 —— 经测量否决(DR-037)**:108 表 demo 逐条测量显示朴素强门槛(MIN_DISTINCT=3/排除唯一子键)会砍 34%、误杀真维度 FK 与 DR-036 自引用;quick_build 维持 compat(实证更稳)。集成安全网已验证(526/531,5 为环境态预存失败)。
- [x] **DR-037 方向原语落地**:`fk_direction`/`should_reverse` 纯函数 + 4 单测;揭示 48 条「child==声明PK」是方向反,应反向而非丢弃。
- [x] **DR-037 接线**:`should_reverse` 接进 quick_build(抑制方向反的边、不污染 seen,让正向自然发现);108 表 demo 266→263 恰抑制 3 条共享键巧合假边、0 真关系丢失、verified 105 不变;集成 526/531 零回归。
- [ ] **DR-038 自适应门槛(需大表 fixture 才动)**:MIN_DISTINCT/唯一度阈值随表体量/基数自适应;当前 demo 全小维度表无从验证,按 DR-037 纪律不做无数据支撑的门槛移植。

## 任务 Tasks(每项一次提交)

1. 建 `dao_core.py` + `test_dao_core.py`(先写一致性测试,含与引擎平价)。
2. quick_build 接入 dao_core(compat),108 表 demo 逐值回归验证零漂移。
3. (下一增量)DR-036 自引用键红-绿;强门槛翻转 + 集成套件护航。

## 验收 Acceptance

- [x] `pytest tests/` 全绿:**63 passed**(IR-007 的 43 + dao_core 20)。
- [x] `dao_core` 覆盖率 83.3%,确定性层 TOTAL 72.9%。
- [x] `ruff check dao_core.py quick_build.py`:F/B 零告警。
- [x] 108 表 demo 逐值回归:关系指纹 + note/evidence 全量一致(compat 迁移零风险)。
- [ ] 强门槛翻转后集成套件 535 断言仍绿(下一增量,需起服务)。

## 备注

- **为何先 compat**:强门槛(MIN_DISTINCT/PK 排除)会改 demo 产物(265→?),须由集成套件护航才敢翻;
  本增量先做**零风险收口**(单一事实源已建立),把行为翻转与自引用键留给带集成护航的下一增量。
- **平价而非改引擎**:上游 `relation_discovery` 属独立仓(自有 specs/CLAUDE.md),本轮以平价测试锁同口径,不跨仓改动。
- 下一步落 DR-036:`test_quick_build.test_build_self_referential_fk_currently_missed` 是现成的红点,
  放开 `pt==t` + 引入角色词典(reports_to↔employee)后,把该断言从「发现不了」反转为「能发现」。
