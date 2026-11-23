# DR-163 · 经 JDBC 桥接实连 HiveServer2

状态：accepted。日期：2026-11-23。依据：Doris 与 HiveServer2 现场只读验收（`tests/field/`）。登记的 Hive 连接此前只能登记，SQL 工作台、图表、表清单与预览一律返回“HTTP 取数暂未接”。

## 决定

- **经 JDBC 驱动访问 HiveServer2。** `hive_bridge.py` 调用 `drivers/HiveJdbcBridge.java`，由本机 JDK 11+ 加载 Hive JDBC 驱动执行；不引入 Python 的 Thrift/SASL 依赖。部署方以 `DATAMIND_HIVE_JDBC_CLASSPATH` 提供驱动，必要时以 `DATAMIND_JAVA` 指定 `java`。有 `javac` 时按源码摘要编译一次并缓存，否则直接运行源文件。
- **Apache Hive 与 Spark Thrift Server 同等对待。** 两者都按 HiveServer2 协议接入；现场身份核对接受产品名含 `Hive` 或 `Spark SQL`。标识符用反引号。
- **只读与凭据。** 只读、单语句校验在调用驱动前完成；口令经标准输入传递，不出现在进程参数中。驱动报错可能含地址与账号，返回前去除。
- **表清单不提交查询作业。** 表名取自 `SHOW TABLES`（失败时退回 JDBC `getTables`），列数先取一次 `getColumns`，失败时在 25 秒内逐表 `DESCRIBE TABLE`；读不到的列数报告为未知，不报 0。数据库名取自 JDBC URL，不调用 `Connection.getSchema()`：Hive 驱动把它实现为 `SELECT current_database()`，是一次查询作业。
- **失败按阶段说明。** 驱动或 JDK 缺失、无法连接、服务端内存不足（OutOfMemoryError）、超时分别给出处理建议。
- **连接状态。** 连接清单记录最近一次实连结果，列表区分“已登记 · 未验证”“已连通（引擎名）”“最近一次连接失败”；只在状态变化时写盘。
- **新建连接的端口。** 未手工编辑端口时，切换引擎即改用该引擎的默认查询端口（Doris 9030、Hive 10000 等）。

## 现场情况（2026-11-23）

现场端点为 Spark SQL 3.2.4（Spark Thrift Server）。登录、`SHOW TABLES`、`DESCRIBE TABLE`、`VALUES` 正常；凡需提交作业的查询（含 `SELECT 1`、`SELECT current_database()`）以及 JDBC `getTables`/`getColumns` 均返回服务端 `OutOfMemoryError: Java heap space`，会话级参数无法绕开。这是服务端 driver 堆耗尽，需集群管理员重启 Thrift Server 或调大 driver 内存；DataMind 侧表清单可用，查询给出上述说明。

## 验收与恢复

需求 HIVE-LIVE-01/02、FIELD-PORT-01、FIELD-HIVE-STATUS-01 对应 `tests/unit/test_hive_bridge.py` 与 `tests/frontend/representation_fidelity_browser.py`；现场验收按 `tests/field/README.md` 运行。回退时撤销本次提交即可；已登记的 Hive 连接记录不受影响，新增的 `live_status`、`live_checked_at`、`engine` 字段可忽略。
