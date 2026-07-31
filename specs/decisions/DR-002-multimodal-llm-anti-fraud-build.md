# DR-002 · 多模态 LLM 本体自动构建 + 真实数据反造假取证(算法核心)

- **状态 / Status**: accepted
- **日期 / Date**: 2026-07-07
- **决策者 / Deciders**: Boss(用户) + Writer(Claude)
- **关联 / Refs**: `server.py` `build_inquire`/`_gather_evidence`/`_llm_extract_ontology`/`_adjudicate_ir`;平台 DR-003(agent 提议+critic)、DR-007(多源)、DR-008(知识包提示)

## 上下文 / Context
「基于多模态 LLM 的本体自动构建」是产品算法亮点。早期实现把构建做成纯确定性 `quick_build`(FK∪取值重叠规则),
LLM 只做首尾意图/摘要——算法亮点被架空。且 LLM 单跑易造假(编造库里没有的关系)。

## 决定 / Decision
本体构建 = **LLM 提议、真实数据裁决**的四步 agentic 管线(SSE 流式):
1. **多模态证据聚合**(`_gather_evidence`):库表结构(按 fact_/dws_ 语义重要度排序防截断)+ 上传的可文本化资产(建表代码/业务文档/JSON/CSV;**Excel 知识包经 openpyxl 取样**,见平台 DR-008)+ 图像/二进制列为「引用证据」。
2. **多模态 LLM 抽取**(`_llm_extract_ontology`):综合结构化库表 + 非结构化文档,输出 objects(name/**cn 中文名**/kind∈object|event|asset|role/table/evidence)+ relations(source/target/verb/rationale);选中技能注入对应方法论(ontology-forge→OWL2/Palantir 四层/SHACL…)。
3. **反造假取证**(`_adjudicate_ir`):LLM 提议的每条关系用真实数据裁决——两端绑真实表且子列取值∩父键**≥60% ∧ 父键唯一 → `verified`**;有据无量 → `candidate`(待取证);对象绑表则补列级 attrs;纯文档概念标 candidate。**LLM 不得自评 verified**。
4. **摘要**:LLM 生成本体说明(基于产物,不编造)。

**硬约束**:
- 反造假门槛与平台一致(取值重叠≥60%∧父键唯一);LLM 离线/超时**回退纯数据驱动 `quick_build`**,方法徽章如实标注(多模态 LLM 抽取 / 数据驱动兜底)。
- 抽取超时预算:`run_turn` 220s、外层 `_bounded` 250s;主引擎限流时兜底备选(见 [[DR-003]])。证据体量控制(schema≤12K + docs 取样)以免撑爆 prompt 致超时。
- 对象 `id` 用英文 name(稳边引用),**显示名用 cn**(有业务意义中文名),见 [[DR-009-object-cn-display]]。

## 后果 / Consequences
- (+) 算法亮点落地:LLM 读多模态证据抽取、真实数据反造假裁决,verified/candidate 逐条可审计;verb 语义边 + 实线/虚线可视化区分。
- (+) 诚实:库/文档没有的不编造;离线兜底可用且标注。
- (−) LLM 步骤耗时(~2-4 分钟)且依赖引擎在线/额度;大 prompt 易超时,靠证据瘦身 + 超时预算缓解。
