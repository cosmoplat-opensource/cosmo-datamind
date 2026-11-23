# Doris 与 HiveServer2 现场只读验收

在能访问目标集群的节点，从仓库根目录用项目的测试 Python 运行。测试通过原生驱动和 DataMind 的 Flask 接口分别取数。DataMind 连接登记写入 pytest 创建的临时工作目录。默认 pytest 套件不会收集此文件。

| 环境变量 | 用途 |
| --- | --- |
| `DATAMIND_FIELD_KIND` | 每轮选择 `doris` 或 `hive` |
| `DATAMIND_FIELD_DSN` | 现场提供的 JDBC URL，含主机、查询端口和数据库。Doris 用 `jdbc:mysql://`，Hive 用 `jdbc:hive2://` |
| `DATAMIND_FIELD_USER`、`DATAMIND_FIELD_PASSWORD` | 从现场凭据管理器注入只读账号和密码。密码可为空 |
| `DATAMIND_FIELD_HIVE_CLASSPATH` | Hive JDBC 驱动及依赖的绝对类路径。现场 Java 需支持直接运行 `.java` 文件。本次使用 JDK 17。未另设 `DATAMIND_HIVE_JDBC_CLASSPATH` 时，DataMind 侧也用这一驱动 |
| `DATAMIND_FIELD_PREVIEW_TABLE` | 可选。已存在的合成验收表，唯一列为整数 `v`，四行分别为零、11、22 和 NULL。未指定就跳过表预览 |

```sh
python -m pytest tests/field/cluster_checks.py -r fs --tb=short
```

1. 原生驱动先核对服务端身份。Hive 从 JDBC 元数据读取数据库产品名，Apache Hive 与 Spark Thrift Server（产品名 Spark SQL）都按 HiveServer2 端点接受。Doris 检查 `@@version_comment` 是否标识 Doris。[Doris 官方连接示例](https://doris.apache.org/docs/dev/connection-integration/data-integration/dbine/)
2. 原生驱动、SQL 工作台和图表取数分别运行同一条两行常量聚合，期望得到一行，合计为 33，缺失列为 NULL。DataMind 响应还须包含 `live: true`。
3. 表清单使用当前账号的可见范围。指定合成表时，同时校验其列数和预览值，包括真实零和 NULL。程序只执行只读查询，Hive JDBC 会话有 45 秒的总时限。

现场通过需要确认原生引擎身份与各项取数断言。预览跳过时，在验收记录中单列。两项独立的浏览器回归可以离线运行。

```sh
python -m pytest tests/frontend/representation_fidelity_browser.py \
  -k 'saved_connection_uses_selected_engine_port or unverified_hive_connection'
```
