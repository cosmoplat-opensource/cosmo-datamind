# DR-038 · 基数自适应 θ:基准增益、真实数据回归、不接入

- **状态 / Status**: accepted(原语落地;**不接入 quick_build**)
- **日期 / Date**: 2026-07-31
- **决策者 / Deciders**: Boss(用户) + Writer(Claude)
- **关联 / Refs**: [[DR-037-direction-aware-adjudication]]、[[DR-039-hallucination-eval-harness]]、[[IR-008-adjudication-core-convergence]];实证=`dao_core.adaptive_theta` + `tests`、反幻觉评测台、108 表 demo 逐条测量

## 上下文 / Context

固定 θ=60 有两类失效(评测台量化):**高基数子键**命中 55% 重叠是强证据却被漏(FN),
**低基数枚举**命中 60% 是弱证据却被收(FP)。直觉解法:θ 随基数自适应——高基数放宽、低基数维持严阈。

## 决定 / Decision

1. **落地 `dao_core.adaptive_theta(child_distinct)`**:阶跃式(distinct≥50→θ50,否则 60),
   拐点与地板可由评测台标定、可回归,不引入难解释的连续曲线。纯函数 + 4 单测。
2. **基准验证为纯增益**:反幻觉评测台上 distinct≥50→θ50,高基数部分重叠真 FK 召回 **0.8→1.0**,
   泄漏率不变——干净数据上是零代价召回增益。
3. **但不接入 quick_build**。原因:在真实 108 表 demo 上逐条测量,自适应 θ 会把 **7 条边
   从 candidate 促成 verified,且 7 条全为假阳**——
   `fact_maintenance_plan.plan_id→fact_sales_plan.plan_id`、
   `fact_production_order.order_id→fact_sales_order.order_id` 等:**不同事实表的同名代理键
   (plan_id/order_id)值域偶合 55–58%**,名同、皆为 PK、父键唯一——
   与基准里的真 FK(`order_events→customers`,孤儿维)**在裁决所用信号上无法区分,真伪却相反**。

## 为什么这样做 / Rationale

- 这是 [[DR-037-direction-aware-adjudication]] 规范的又一次应验:**基准上的纯增益,在杂乱真实数据上是精确率回归**。
  「真 FK 带孤儿」与「同名代理键区间偶合」用 基数+重叠+命名+唯一 四信号**无法区分**——
  正是评测台揭示的孪生案例在真实库的翻版。
- 分开「真 FK 带孤儿」与「跨事实表同名代理键偶合」,需**判别性信号**:
  包含方向的强弱、基数分布、跨事实表结构约束(一事实表的 PK 不应是另一事实表的 FK)、
  空值率——即**多信号裁决**,不是一个 θ 旋钮。故 θ 自适应单独上马**弊大于利**。

## 影响 / Consequences

- (+) `adaptive_theta` 原语 + 评测台对照实验落地并测试;何时安全启用有了称量工具。
- (+) 又一次用目标数据实测挡住「看着像提纯、实则回归」的改动(demo 7 条假阳未落地)。
- (−) 高基数部分重叠真 FK 暂仍被固定 θ 漏(召回损失),待多信号裁决能判别后再放宽。
- **决定**:quick_build 维持固定 θ=60;`adaptive_theta` 保留为**已测原语**,接入前置条件=
  多信号裁决能把「同名代理键偶合」与「真 FK 带孤儿」分开(未来 DR)。
- **规范强化**:门槛类改动一律「基准验证 + 目标数据回归」双关,单靠基准增益不足以上线。
