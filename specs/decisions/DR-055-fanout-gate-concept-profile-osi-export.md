# DR-055 · 扇出关卡、概念画像与 OSI 风格导出

- **状态 / Status**: accepted
- **日期 / Date**: 2026-09-06
- **关联 / Refs**: [[DR-054-metric-contract-and-lifecycle]]、[[DR-019-deepqa-upgrade]]、[[DR-033-selected-ontology-as-anchor]]、[[DR-049-relational-projection-and-explicit-pipeline]]、[[DR-016-mcp-action-server]]

## 上下文 / Context

- 口径拦截只管「表在不在白名单、JOIN 键落不落已验证关系」,不管粒度:一条合规 SQL 把订单表
  JOIN 到明细表再 `SUM(amount)`,键对合法,结果按子表行数放大。此前只靠提示词里的口径要求,
  是概率性的。关系证据里记录了父键唯一性,基数方向其实已知。
- 问数锚定靠关键词命中零散表列,依赖问句恰好命中列名;OSI 分享的对照实验里,
  预计算「概念画像」再检索、只保留两个核心工具的路线首答成功率最高,原因是把图探索提前、
  减少工具选择方差。该数字来自对方 22 题的评测,只作方向性依据。
- OWL 与关系表投影之外,与 dbt / Snowflake / BI 侧交换需要厂商中立的语义模型格式。

## 决定 / Decision

1. **扇出关卡**(`server._validate_sql_ontology` 第 ③ 步):按关系证据识别 JOIN 的父侧表;
   对父侧表限定列做 `SUM/AVG/COUNT(列)` 即拦截,给出改写提示(先在子表聚合到父粒度再 JOIN,
   或 `COUNT(DISTINCT)`)。`COUNT(DISTINCT)`、`MIN/MAX`、子侧列、未限定表名的列不拦。
   父侧识别先于「同名键放行」,扇出与键名无关。
2. **概念画像**(`concept_profile.py`):每个对象确定性聚合名称/别名/定义/属性中文名/
   沿 verified/asserted 关系可达的邻居及动词/绑定指标;关键词检索按名称 5 分、列 2 分、
   指标与关系 1 分,同分按键名排序。`GET /api/ont/profile` 暴露检索与全量画像。
   本决定**不**切换问数默认锚定路径:先用 `benchmark/qa_set.json` 加一组画像检索对照,
   有数再决定。
3. **OSI 风格导出**(`osi_export.py`,`GET /api/export/osi`):从同一份 IR 生成
   datasets / relationships(含 multiplicity、verbalizes、状态与证据)/ metrics(含契约与编译 SQL)/
   concepts(extends→BFO/IOF、定义、映射)。自带确定性 YAML 发射器,不引入 pyyaml。
   **未经 OSI 官方 validator 校验**,文档只写「可导出 OSI 风格 YAML」。
4. **MCP 只读语义工具**:`search_concept`、`describe_metric`、`query_metric`;写路径仍只有
   `invoke_action`。`query_metric` 只放行 `certified` 指标。

## 后果 / Consequences

- 扇出关卡会拦下部分此前放行的 SQL;评测 C 组的口径拦截计数如实计入。
- 路由 +2(`bp_semantic.py`);MCP 工具 3→6,只读工具的 `readOnlyHint` 由单测锁定。
- 画像与 OSI 导出均为只读派生物,不接受回写。
