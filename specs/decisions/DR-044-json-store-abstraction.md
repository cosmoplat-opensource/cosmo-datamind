# DR-044 · JSON store 持久化抽象

- **状态 / Status**: accepted(抽象与测试落地;server 增量迁移)
- **日期 / Date**: 2026-07-31
- **决策者 / Deciders**: Boss(用户) + Writer(Claude)
- **关联 / Refs**: [[PROPOSAL-sdd-tdd-improvement]]、[[DR-045-engineering-harness-and-tdd]];实证=`store.py`、`tests/unit/test_store.py`

## 上下文 / Context

架构审计点名:`server.py` 有 **15 个手搓 JSON store + 64 处散落 `json.load/dump`**,
无 schema/校验/迁移,加一个字段要改多处;`_atomic_json` 是安全叙事却**无负向持久化测试**
(坏文件恢复 / 原子写不留残片 / 并发写)。

## 决定 / Decision

把「workdir 下单文件 JSON 持久化」收敛为一个可测抽象 `store.JsonStore`,供 server 增量迁移:

1. **原子写**:tempfile + os.replace(与 `server._atomic_json` 同纪律),崩溃不留半截文件。
2. **坏文件优雅恢复**:读到非法 JSON 退回 default 而非崩溃——一处坏档不打崩整个端点。
3. **可选 schema 校验**:save 前校验形状,非法抛 ValueError 且**不破坏旧值**。
4. **可选迁移钩子**:load 时按版本迁移旧结构。
5. **并发安全**:每 path 一把可重入锁,`update()` 读-改-写全程持锁,无丢更新
   (测试实证 4 线程×50 次并发 update → 精确 200,无丢更新)。

## 迁移纪律(为何不即刻改 64 处)/ Rationale

- **按文件整体迁移,不逐点掺入**:若一个文件的部分访问走 `JsonStore`(自带锁)、
  另一部分仍走 `_atomic_json`(server `_WRITE_LOCK`),同文件出现**两套锁**,反而不如原状安全。
- 故迁移单位是「一个文件的全部读写一次性切到同一 store」,每次切换由集成套件(535 断言)护航。
- 本 DR 先落**抽象 + 负向测试**(填审计两处空白:无抽象、无负向持久化测试),
  server 各 store 按上述纪律逐文件迁移,不在一次提交里翻 64 处。

## 后果 / Consequences

- (+) 持久化抽象与坏文件/原子/并发的负向测试落地(7 单测,含并发原子性)。
- (+) 加字段/改结构有了单点:schema 校验 + 迁移钩子,不再散落。
- (−) server.py 现有 64 处暂未迁移;按逐文件纪律增量切换(独立后续任务,每次集成护航)。
- **边界**:仅覆盖单文件 JSON store 场景;只读 SQLite 数据底座与图谱 IR 铸造各有其路径,不在本抽象内。
