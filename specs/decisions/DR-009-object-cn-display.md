# DR-009 · 本体对象:id 用英文名、显示用中文名(cn)

- **状态 / Status**: accepted
- **日期 / Date**: 2026-07-07
- **决策者 / Deciders**: Boss(用户) + Writer(Claude)
- **关联 / Refs**: `server.py` `ir_to_graph`;`_llm_extract_ontology`/`_adjudicate_ir`(cn 字段);平台 DR-001(关系动词中文)

## 上下文 / Context
构建出的对象/事件显示成英文表名/键名(dim_customer),不可读。排查:LLM 抽取**本就生成了 cn**(dim_customer→客户),
但 `ir_to_graph` 的应用/构建本体分支显示名误用了英文 `name`(示例 分支用的是 `cn`)——两分支不一致的显示 bug。

## 决定 / Decision
- **id 稳定用英文 name**(边引用 source_concept/target_concept 依赖它,不可改);**显示名一律 `o.get("cn") or o["name"]`**,应用/构建/demo 三分支统一。
- LLM 抽取契约:每个对象须有 `cn`——**有业务意义的中文名**(客户/销售订单/退货事件),优先复用表注释与上传文档/Excel 知识包中文术语,**禁拼音或照搬英文表名**(prompt 硬约束)。
- 节点详情、内联预览、指标血缘均以 cn 优先展示;导出(TTL/OWL/JSON-LD)local() 名仍用英文标识(互操作),cn 作 label/注解。

## 后果 / Consequences
- (+) 图谱/预览显示「企业/客户/销售订单」等有意义中文名;边引用不受影响。
- (+) Excel 知识包术语反哺命名(见平台 DR-008),中文名更贴业务口径。
- (−) quick_build 兜底产物无 cn(退回显示英文表名,honest 标注「数据驱动」);LLM 路径才有 cn。
