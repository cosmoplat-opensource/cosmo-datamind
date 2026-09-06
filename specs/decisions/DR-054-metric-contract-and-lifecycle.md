# DR-054 · 指标契约、指标反解与指标生命周期

- **状态 / Status**: accepted
- **日期 / Date**: 2026-09-06
- **关联 / Refs**: [[DR-011-generalized-adjudication]]、[[DR-019-deepqa-upgrade]]、[[DR-026-doubleblind-intent-and-usage]]、[[DR-049-relational-projection-and-explicit-pipeline]]、[[DR-050-executable-ontology-build-gate]];外部依据:Open Semantic Interchange 分享(DataFunSummit,2026-08)

## 上下文 / Context

关系早已是「模型提议、`dao_core` 裁决、`verified` 只来自可回放证据」,指标却停留在文本:
`metric_layers` 只有名称/说明/绑定表/取值列,聚合方式在查询时按名称正则猜,日期列现场找。
指标转不成稳定 SQL,也无法与任何参照结果比对——这正是 OSI 分享指出的「指标只是文本描述,
无法执行、归因、下钻,就形不成工程闭环」。
构建提示词只让模型提议对象与关系,不提议指标;而三类可用的指标来源已经在仓库里:
上传的视图/ETL SQL、看板口径表(Excel)、沉淀的问数 SQL 与参考基准 `gold_sql`。

## 决定 / Decision

1. **指标契约**(`metric_contract.py`):指标声明「怎么算」——绑定对象、度量列、聚合、过滤、
   时间列与粒度、可用维度;`compile_sql` 确定性编译,同一契约逐字节一致;`schema_missing`
   先查目录再执行(SQLite 会把未知双引号标识符当字符串,不查目录会给不存在的列发证据)。
2. **裁决与关系对称**:`adjudicate` 只读执行编译结果并与参照比对(标量对标量;分组结果
   按参照形状重新编译后逐行比对)。`verified` 只来自「可执行 ∧ 与参照一致」;可执行但无参照仍是
   `candidate`;`certified` 只能由人授予(等价于关系的 `asserted`),`deprecated` 人工停用。
   人工状态不被后续核验覆盖,证据照记。
3. **三入口反解**(`metric_mining.py`):历史 SQL(聚合表达式→原子指标,GROUP BY→维度,
   `substr(日期,1,7)`→月粒度)、口径表(名称/说明/计算逻辑,含运算符者记派生并保留公式原文)、
   沉淀 SQL 与参考基准(既是候选也是参照)。只做确定性文本解析;含 JOIN 的语句不猜口径,
   登记为待人工拆解;WHERE 含 OR/子查询时放弃过滤并如实标注、不把原 SQL 当参照。
4. **构建收尾接入**(`metric_pipeline.run`):在关系裁决与合并之后、验收检查之前执行;
   失败只留痕,不影响关系产物。`upsert_layers` 同名按状态取高,旧形状指标原样保留。
5. **验收检查**:`verified` 指标须携带「编译 SQL + 执行成功 + 与参照一致 + 参照来源」,
   `certified` 须有确认人,否则阻断;candidate 指标计入待审。
6. **消费分级**:口径卡按 `certified > verified > candidate` 排序并标注状态;`/api/metric/quick`
   契约指标按契约编译,旧指标才走启发式并标 `status=heuristic`;问数上下文给出口径并要求
   不得改用其它聚合;对外 MCP `query_metric` 只放行 `certified`。
7. **使用驱动晋升**:口径卡命中即计一次指标使用度;`usage_stat.report` 对「高频 × candidate」
   指标给出高优先级建议。
8. **维度可达性**:`allowed_dimensions` 只承认绑定表自身列与沿 verified/asserted 关系一跳可达
   的对象;处于父侧的关联对象标 `fan_out_risk`。

## 后果 / Consequences

- 指标第一次成为带证据的一等产物;`metric_report` 随图谱落盘,反解来源、跳过项、拒绝项可审计。
- 既有图谱的旧形状指标全部为 candidate,验收结果会出现「指标待审」——这是如实反映,不是回归。
- 契约 v1 只覆盖单实体原子指标与保留公式的派生指标;跨表指标、窗口/留存/漏斗不在范围内。
- 路由 +4(`bp_metrics.py`);验收检查报告新增 `metrics` 段;关系型投影 `ont_metric` 增契约列与
  `v_consumable_metric` 视图。
