# DR-035 · 单一裁决核 dao_core(消两份漂移实现)

- **状态 / Status**: accepted
- **日期 / Date**: 2026-07-31
- **决策者 / Deciders**: Boss(用户) + Writer(Claude)
- **关联 / Refs**: [[DR-002-multimodal-llm-anti-hallucination-build]]、[[DR-011-generalized-adjudication]]、[[IR-008-adjudication-core-convergence]];实证=`dao_core.py`、`quick_build.py`、`tests/unit/test_dao_core.py`、108 表 demo(demo_metrics.db)265/104 逐值回归

## 上下文 / Context

裁决逻辑此前有**两份漂移实现**,门槛已经不一致:

| 轴 | `quick_build.py`(datamind 现场构建) | `../ontology-engine/engine/relation_discovery.py`(引擎) |
|---|---|---|
| 重叠公式 | `100*|c∩p|/|c|` 内联 | `overlap_pct` 四舍五入 |
| θ | 60 内联 | `MIN_OVERLAP=60.0` 命名 |
| 父键唯一 | 精确 100%(COUNT==COUNT DISTINCT) | 0.95 近似 |
| 子键 distinct 下限 | **无** | `MIN_DISTINCT=3` |
| 排除「子键即主键」 | **无** | 有 |
| 命名校验 | `key_name_ok`(两键词根) | `name_score`(含父表名/异名同义) |
| 非唯一父键 | 落弱 candidate(`elif ov>=20`) | 直接跳过 |
| 自引用键 | 跳过(`pt==t`) | 跳过(`pt==ct`) |

两份各自演化,`quick_build` 少了 MIN_DISTINCT 与 PK-作子键排除,**结构上比它声称对齐的引擎更易假阳**;
审计并指出两处此前未察觉的漂移(非唯一父键处置、命名校验口径)。

## 决定 / Decision

1. **抽出单一裁决核 `dao_core.py`**:自包含、零外部依赖(与本仓「quick_build 零依赖」一致),
   **与数据访问方式无关**——只吃「已抽取信号」(子/父键 distinct 集合、父键是否唯一、列名),
   故 quick_build 的 SQL 路径(180k 行大表)与引擎的内存路径可喂各自信号、共用同一判定。
2. **收敛原语**:`overlap_pct`(保留原始精度,不四舍五入,边界比较与内联算法逐值一致)、
   `key_stem`/`key_name_ok`(quick_build 口径)、`name_score`/`_core`(引擎口径,更富)、
   `is_pk_like`、常量 `MIN_OVERLAP/MIN_DISTINCT/UNIQUE_PK_RATIO/WEAK_FLOOR`,全部单一事实源。
3. **统一裁决决策 `classify(...)`**:三态 verified/candidate/drop;规范默认=强门槛
   (`min_distinct=3`、`exclude_pk_child=True`);**compat 模式**(`min_distinct=1`、
   `exclude_pk_child=False`)复现 quick_build 历史行为。
4. **quick_build 先以 compat 模式接入**:决策走 `classify`,命名/重叠走 dao_core 原语,
   **产物在 108 表 demo 上逐值不变**(265 关系/104 verified,note 与 evidence 全量一致)。
   强门槛的翻转(启用 MIN_DISTINCT/PK 排除)与自引用键(DR-036)另作增量,需集成套件回归验证后再落。

## 后果 / Consequences

- (+) 裁决口径单一事实源;两份漂移的原语不再各自定义,新改进只需改 dao_core 一处。
- (+) `classify` 可被一致性测试与未来强模式共用;`test_dao_core` 含与引擎 `name_score` 的**平价测试**,证明同口径。
- (+) 迁移零风险:compat 模式在真实 108 表库上逐值回归通过(含 note/evidence 文本)。
- (−) 上游 `relation_discovery` 属独立仓(自有 specs),本轮不改;dao_core 作为参考实现,以平价测试锁同口径。
- **遗留**:强门槛翻转与 DR-036 自引用键——`test_quick_build` 已留「现状锁定 + 落地后反转」的自引用键断言作路标。
