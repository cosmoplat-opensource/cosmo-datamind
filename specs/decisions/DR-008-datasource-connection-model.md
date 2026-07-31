# DR-008 · 数据源与连接模型(内置 / SQLite 校验 / 外部库登记)

- **状态 / Status**: accepted
- **日期 / Date**: 2026-07-07
- **决策者 / Deciders**: Boss(用户) + Writer(Claude)
- **关联 / Refs**: `server.py` `build_sources`/`build_connect`/`_resolve_src`/`_sqlite_tables`;对齐平台『配置数据源』

## 上下文 / Context
本体构建、数据连接、数据可视化都需选数据源;要支持「连接多个数据库」,但 DataMind 本地执行(见 [[DR-001]]),
无法在离线环境直连远端 MySQL/Doris/Hive。需在「可用」与「诚实」间取舍。

## 决定 / Decision
- **三类源**,统一经 `_resolve_src(id)` 解析为 (sqlite 路径, 名称):
  - **内置**:`imom`(主库)、`uploads`(上传库),随系统就绪。
  - **SQLite 文件**:登记时**实时校验可读表**(`_sqlite_tables`),就绪即可作构建/取数/浏览源。
  - **外部库**(mysql/doris/hive/postgresql):仅**登记连接串**(前端按 host/port/db 自动生成 DSN),标注「需内网/驱动」;离线不直接取数,`_resolve_src` 返回 `(None, 名称)`,调用方给出清晰离线提示。**不虚假宣称能取数**。
- 连接持久化于 `workdir/build_connections.json`(`_atomic_json`);默认选中源为**就绪源**(空的上传库自动兜底到首个就绪源,避免「无可读表」报错)。

## 后果 / Consequences
- (+) 「连接多个数据库」可用:SQLite 真实可接、外部库可登记展示;取数只在本地可读源发生(安全,见 [[DR-001]])。
- (+) 诚实:外部库如实标注需驱动,不假装联通。
- (−) 生产远端库实际取数需另接驱动(planned);当前外部库仅登记元数据。

## 幂等连接登记(2026-07-20 实操点检发现)
**实操发现**:数据连接页出现两条完全相同的「示例制造数据库」(同 name/kind/path),构建页数据源清单同样重复——`build_connect` 每次 POST 无条件 insert,同一物理源重复登记堆成脏列表。
**修复**:`/api/build/connect` 登记前按物理源身份查重(sqlite 按 `path`、外部库按 `dsn`),命中则复用既有连接并回 `deduped:true`,不新增;存量 `build_connections.json` 一次性去重(2→1,原子写)。回归 +1(『重复连接去重(同源复用)』)→ **114/114**。非破坏:仅防重复,不删既有唯一连接。
