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
   **锚定对照结果(2026-09-27,`scripts/eval_anchoring.py`,记录于 `benchmark/anchoring_eval.json`)**:
   端到端三组对照需要 LLM 引擎,当前环境不可用;画像只改变「问句→表」这一步,故按参考基准的
   `gold_tables` 离线度量锚定阶段(k=5,示例本体,8 题):

   | 锚定方式 | 平均召回 | 平均精度 | 全部 gold 表命中 | 平均带入表数 |
   |---|---|---|---|---|
   | 现行关键词锚定(`build_context`) | 0.958 | 0.087 | 7/8 | 13.2 |
   | 概念画像检索 | 0.792 | 0.467 | 6/8 | 2.5 |

   画像把上下文收窄到约五分之一,但召回下降。漏召的根因是设计取舍而非缺陷:画像只认完整名称
   出现在问句中(防「单」误中「工单」),业务名「产出记录」接不住问句里的「生产产出」;
   关键词锚定按分词反向匹配并做术语扩展,能接住。锚定阶段漏掉一张必需表会让问题直接不可答,
   多带几张表只增加上下文噪声,召回比精度更要紧。
   **决定:不切换问数默认锚定路径。** 画像保留用于 `/api/ont/profile` 与 MCP `search_concept`
   (面向人与外部 Agent 的概念查找,精度更重要)。
   不在同一份 8 题集上调整画像匹配规则后再宣称改进——那是对评测集过拟合。若要推进
   「关键词召回 + 画像重排」的混合方案,须先准备独立的、规模更大的留出问题集。
3. **Apache Ossie 导出**(`osi_export.py`,`GET /api/export/osi?kind=semantic_model|ontology`):
   OSI 已进入 Apache 孵化器并更名 Apache Ossie。两份官方 machine-readable schema
   (`core-spec/ossie-schema.json`、`ontology/ontology.json`,version const `0.2.0.dev0`)按固定
   revision 快照到 `ontology/standards/ossie/`,与既有 BFO/IOF/ISA-95 资产同一套做法。
   导出形状以 schema 为准:
   - 核心语义模型:`datasets`(source/primary_key/fields,fields 带 `expression.dialects[ANSI_SQL]`
     与 `datatype`,日期列自动带 `dimension.is_time`)、`relationships`(from/to/from_columns/to_columns)、
     `metrics`(单条 ANSI SQL 聚合表达式,过滤折进 `CASE WHEN`,不依赖 WHERE 子句)。
   - 本体:`concepts`(EntityType,`extends` 指向 BFO/IOF 上层类别)与 `relationships`
     (`roles`/`multiplicity`/`verbalizes`)。`ontology_mappings` 暂不导出——其 schema 以远程
     `$ref` 引用核心 schema,离线校验会触发网络解析。
   - schema 各层均 `additionalProperties: false`,故本仓的状态、证据、来源一律走
     `custom_extensions`(vendor_name=`datamind`,data 为 JSON 字符串),不污染标准字段。
   - 只有 verified/asserted 且键齐全的关系才进 `relationships`(消费方会当 JOIN 路径用),
     只有有契约的指标才进 `metrics`;其余放进模型级 `custom_extensions`,不提升也不丢失。
   `osi_export.validate()` 与 `scripts/validate_ossie_export.py` 用快照 schema 校验;
   jsonschema 缺失时返回「未校验」而不是假装通过。示例本体的两类导出均已通过官方
   `validation/validate.py`(Ossie 仓库 b740402)。
4. **MCP 只读语义工具**:`search_concept`、`describe_metric`、`query_metric`;写路径仍只有
   `invoke_action`。`query_metric` 只放行 `certified` 指标。

5. **界面**(DR-054 的呈现面):指标中心按图谱切换,列出状态与契约口径;详情面板给出编译 SQL、
   核验证据(是否执行、与参照是否一致、参照来源)、可下钻维度(父侧标扇出风险)与来源;
   人工操作只有「确认口径 / 停用 / 撤回」,界面不存在把指标置为 `verified` 的入口,
   确认口径必须填确认人。口径卡与即时问数显示状态,未核验口径明确标注「数值仅供参考」。

6. **中文表名后缀剥离收敛**:业务问句说「销售订单」,本体里叫「销售订单事实表」。
   该约定此前在 `server` 的指代延续与根因诊断各抄一份,概念画像检索一开始漏了它,
   导致最常见的中文命名方式召回为零。现收于 `ir_shape.strip_cn_suffix / name_variants`,
   三处共用;`cq_check` 的验收判定刻意保持严格匹配,不使用变体。

7. **待审队列按类型限流**:一种信号刷屏(上百个孤岛对象)会把「指标待核验」挤出 100 条上限。
   `build_quality` 改为每类最多 20 项并给出「另有 N 项」提示,再截断到 100。

## 后果 / Consequences

- 扇出关卡会拦下部分此前放行的 SQL;评测 C 组的口径拦截计数如实计入。
- 路由 +2(`bp_semantic.py`);MCP 工具 3→6,只读工具的 `readOnlyHint` 由单测锁定。
- 画像与 Ossie 导出均为只读派生物,不接受回写。
- `jsonschema` 进 `requirements-dev.txt`:缺失时 schema 校验测试跳过而非失败。
- Ossie schema 仍是 `0.2.0.dev0` 开发版,上游改版时需重新快照并重跑导出校验。
