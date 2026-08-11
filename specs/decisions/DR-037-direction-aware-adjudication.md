# DR-037 · 方向感知裁决;否决「朴素强门槛翻转」(数据实证)

- **状态 / Status**: accepted
- **日期 / Date**: 2026-07-31
- **决策者 / Deciders**: Boss(用户) + Writer(Claude)
- **关联 / Refs**: [[DR-035-unified-adjudication-core]]、[[DR-036-self-referential-and-role-keys]]、[[IR-008-adjudication-core-convergence]];实证=108 表 demo(imom_metrics.db)逐条测量 + 集成套件 526/531;`dao_core.fk_direction/should_reverse`

## 上下文 / Context

IR-008 原计划把 quick_build 由 compat 门槛翻到「规范强门槛」
(`MIN_DISTINCT=3` + 排除「子键即主键」,取自上游 `relation_discovery`)。
翻转前在真实 108 表 demo 上**逐条测量**,结论:**朴素强门槛会误杀真关系,不是提纯**。

**测量结果(266 关系 → 强门槛后 ~175,砍 34%):**

| 门槛 | 砍掉 | 其中真关系样本 |
|---|---|---|
| `MIN_DISTINCT=3` | 11 条 | `dim_business_unit.enterprise_id→dim_enterprise`(distinct=1,真层级)、**`dim_supplier_category.parent_id→自身`(distinct=2,正是 DR-036 刚补的自引用)** |
| 排除「子键唯一」 | 80 条 | `dim_bom.product_id→dim_product`(verified,真维度 FK,仅因 15 行表里 product_id 恰唯一)、`dim_factory.bu_id→dim_business_unit`(verified,真 N:1) |

**根因**:上游强门槛是为**大体量运营表**标定的;星型 schema 的**小维度表**里,
FK 列在少数几行下天然「低基数」或「恰好唯一」,被强门槛误判为噪声。
而 quick_build 早已有的**命名闸**(`key_name_ok`,DR-033)已经拦住了强门槛想拦的
「自增代理键值域巧合」——再叠 `MIN_DISTINCT`/PK 排除属**重复且有害**。

**进一步拆分那 80 条「子键唯一」:**
- 48 条 **child==声明PK**(如 `dim_bom.bom_id(PK)→dim_bom_line`):方向多半是**反的**
  (真方向是对方引用本表 PK)。但因 `seen` 去重,直接 drop 会**连关系一起丢**,而非去冗余——
  正解是**反向**(定向),不是丢。
- 32 条 **唯一但非声明PK**(如 `dim_bom.product_id→dim_product`):多为真 1:1 / 维度 FK,**应保留**。

## 决定 / Decision

1. **否决朴素强门槛翻转**:quick_build **维持 compat 门槛**——已在 108 表 demo 逐值验证,
   是本数据形态下召回更稳的选择;命名闸已覆盖假阳顾虑。
2. **真问题是方向,不是丢弃**:「child==声明PK 且 parent 不唯一」的边是**方向反了**,
   应由**包含方向测试**定向后**反向保留**,而非 drop(保召回 + 纠方向)。
3. **落地方向原语(本增量)**:`dao_core.fk_direction(a_unique,b_unique)`(唯一侧为父:
   a->b/b->a/ambiguous(1:1)/none(多对多))与 `should_reverse(child_unique,parent_unique)`
   (child 唯一而 parent 不唯一→方向反)。纯函数 + 单测,**零风险**,不改 quick_build 产物。
4. **接线落地(本增量)**:`should_reverse` 接进 quick_build——`ov≥60 且 child 唯一而 parent 不唯一`时
   **抑制该边、不污染 seen**,让正向在处理多侧表时自然发现。
   实测 108 表 demo:**266→263,恰抑制 3 条**——均为共享维度键的**巧合值域重叠假边**
   (`dim_bom.product_id→DWS.dim_line_id` ov80%:product_id 与 line_id 数值偶合,两者真属主都是 dim_product);
   **0 条真关系丢失**(`dim_bom/routing/pricing→dim_product` 正向全在),verified 105 不变。
   集成套件 **526/531 与改前一致**(改产物零回归;5 为 apply/undo/engine 环境态预存失败)。
   单测含反向可找回场景(`test_direction_dim_pk_matched_by_fact_is_fact_to_dim`)。
5. **自适应门槛归 DR-038**:`MIN_DISTINCT`/唯一度阈值应随表体量/基数自适应(小维度表放宽),不设死值。

## 后果 / Consequences

- (+) 用数据挡住一次「看着像提纯、实则砍 34% 含真关系」的错误翻转——TDD/逐条测量的价值。
- (+) 方向原语落地并测试,为「反向而非丢弃」铺好可测底座;集成安全网已验证可用(526/531)。
- (+) 澄清收敛口径:**不是盲目采纳上游门槛**,而是每个轴取「本数据实证更优」者(命名闸留、强门槛弃、方向补)。
- (+) 接线后 demo 净抑制 3 条巧合值域重叠假边,0 真关系丢失,集成零回归——方向测试作为**共享键假边的第二道闸**兑现。
- **纪律**:门槛移植必须先在目标数据上逐条测量再定,禁止「因为上游这么写就照搬」。
