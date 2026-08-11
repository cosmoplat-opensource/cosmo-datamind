# DR-043 · 单体路由蓝图化(计划 + 已起步)

- **状态 / Status**: accepted(方案定;拆分本身按 IR-011 分步执行)
- **日期 / Date**: 2026-07-31
- **决策者 / Deciders**: Boss(用户) + Writer(Claude)
- **关联 / Refs**: [[DR-044-json-store-abstraction]]、[[DR-035-unified-adjudication-core]];实证=`server.py`(5091 行/121 路由)

## 上下文 / Context

架构审计头号债务:`server.py` 5000+ 行 / 121 路由 / 单文件,5 个巨函数,跨切面逻辑重复。
但它承载 526/531 集成断言的行为契约,**贸然拆会打碎基线**。

## 决定 / Decision

分两步走,**先降耦合再拆路由**,每步集成套件回归验证:

1. **抽纯逻辑成可测模块(已起步,持续)**:把算法/评测/持久化从路由体里剥出来——
   已完成 `dao_core`(裁决核)、`hallucination_eval`/`definition_eval`(评测)、`store`(持久化)、
   并把 server 的命名校验副本收敛到 dao_core(-18 行)。这些**本可堆在 server.py 里**,
   现在是独立单测模块,server 只调不重写。降耦合让后续拆路由更安全。
2. **路由按簇拆 Flask blueprint(scoped 到 IR-011)**:六簇(ontology/build/deepqa/actions/skills/engine)。
   前置=建**共享上下文模块**(DB 路径/`ro_connect`/`_atomic_json`→`store`/缓存/锁/公用 helper),
   各 blueprint 从中 import;**一次拆一簇**,拆完立刻跑 535 集成断言,绿了再拆下一簇。

## 为什么分步 / Rationale

- **不在一次提交里翻 121 路由**:蓝图拆分要移动大量共享全局态(JOBS/缓存/锁/DB 句柄),
  一次性动手极易破坏 526 基线且难定位。逐簇拆 + 每簇集成验证,是唯一可回滚、可追责的路径。
- **先降耦合的复利**:每抽出一个纯模块(dao_core/store/…),server 的隐式依赖就少一分,
  蓝图拆分时要搬的共享态随之减少。故第 1 步不是拖延,是给第 2 步铺路。

## 影响 / Consequences

- (+) 已把裁决/评测/持久化/命名校验从 server 剥为 4+ 个独立单测模块;server 减重、职责变清。
- (+) 蓝图拆分有了明确、可回滚的分步方案与前置(共享上下文模块)。
- (−) 121 路由仍在单文件;blueprint 拆分按 IR-011 逐簇推进,不在本会话内强行完成
  (贸然拆会危及 526 集成基线,违背本项目「改产物必集成回归验证、逐步验证」的规范)。
- **边界**:本 DR 只定方案与前置;每簇拆分的验收在 IR-011 逐条记录。
