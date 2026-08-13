# DR-001 · 本地只读执行(避开平台跨库/方言/沙箱/权限坑)

- **状态 / Status**: accepted
- **日期 / Date**: 2026-07-05
- **决策者 / Deciders**: Boss(用户) + Writer(Claude)
- **关联 / Refs**: `server.py` `q()`/`sql_is_readonly()`;`ui/qaknow.html`;`ui/rootcause.html`;平台深度问数 1142/information_schema 根因调查

## 上下文 / Context
平台侧 chat-bi 深度问数在真环境反复失败:出图 SQL 丢 catalog 前缀→裸表名落内部 Doris→`1142 SELECT command denied`(权限);
schema 自省又套用 Paimon/Spark 不支持的 `information_schema` 方言;叠加沙箱/配额,聚合从未执行→报告=本体推演+1 行样本(幻觉)。
DataMind 作为产品化前端,若也直连远端仓,会继承全部坑,且离线不可用。

## 决定 / Decision
DataMind 的所有 **SQL 类**分析执行(深度问数 / 数据可视化 / SQL 工作台 / 指标即时问数)**一律在本地 `../demo_metrics.db`(SQLite,108 表 186,833 行合成真数据)只读执行**:
- 连接以 `file:<path>?mode=ro` 打开;`sql_is_readonly()` 仅放行 `select`/`with` 开头且无内嵌写关键字;SQLite 单句 `execute` 阻断堆叠语句。三重防写,即便 SQL 含写也被引擎层拒。
- 语句级 8s 超时(`set_progress_handler`)防笛卡尔积拖垮进程。
- 上传数据经 `attach_uploads` 以只读 `up.<表>` 挂载,供带附件问数。
- 外部库(MySQL/Doris/Hive/PG)仅**登记**连接信息(见 [[DR-008-datasource-connection-model]]),离线不直接取数,如实标注「需驱动」。
- **SPARQL 不走 SQL**:它在本地 **rdflib RDF 图**(由本体 IR → `to_turtle` 现构)上执行,也是本地/只读,但substrate 是语义图而非 SQLite;其安全约束(禁 SERVICE/FROM 外链、软超时)见 [[DR-006-security-model]]。

## 后果 / Consequences
- (+) 无跨目录/权限/方言/沙箱依赖,断网可用;喂真实 schema + 本地执行 + 护栏 → 深度问数一次出正确数据与拆解(实测毛利率 20.7→13.7→9.8% 与 ground truth 吻合)。
- (+) 安全面收敛:写操作在引擎层不可能发生。
- (−) 数据是本地合成副本,非生产实时数据;生产接入需另走 [[DR-008-datasource-connection-model]] 的连接层 + 对应驱动(planned)。
- 差别不在引擎,在「喂真实 schema + 本地执行 + 有护栏」这套编排(qaknow.html 结论)。
