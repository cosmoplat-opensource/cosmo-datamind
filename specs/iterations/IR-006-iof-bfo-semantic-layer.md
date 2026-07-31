# IR-006 · IOF/BFO 语义工程层(注释 / 接地 / 导出 / 完备度 / 一键升级)

- **状态**: delivered
- **关联**: [[DR-010-iof-bfo-alignment]];衔接 [[DR-002-multimodal-llm-anti-fraud-build]]、[[DR-009-object-cn-display]]、[[DR-001-local-readonly-execution]]

## 目标 / Goal
参照 Industrial Ontology Foundry(github.com/iofoundry/ontology),给 DataMind 本体全链路(抽取→裁决→呈现→导出→查询→质量→升级)加一层 BFO 上层归类 + `iof-av` 机读注释 + 有根据关系接地 + SHACL 门禁,并把真实 示例 图谱升级到 IOF 标准。

## 交付 / Deliverables
- [x] **注释 schema**:对象补 `bfo/definition/isPrimitive/example/counterExample/maturity/provenance`,关系补 `founded_relation/temporal`;`_llm_extract_ontology` prompt 加定义纪律;`quick_build.py` 兜底路径同构补齐(标原始概念)。
- [x] **关系接地**:`_FOUNDED_RELATIONS`+`_ground_verb`;`ir_to_graph` 对新旧 IR 统一透传/回溯接地。
- [x] **注释化导出**:`_ir_to_turtle` 自包含 IOF OWL2(BFO subClassOf + iof-av + subPropertyOf + 字段 DatatypeProperty);`/api/sparql`、`/api/ont/forge` 同底 → 注释可 SPARQL 查询、可 SHACL 校验。**未改平台 export_owl.py**。
- [x] **SHACL 门禁**:`_IOF_SHACL`(非原始类须有定义、类须有 label);forge 用 pyshacl 校验返 conforms/violations。
- [x] **完备度记分卡**:`/api/ont/completeness/<key>`;UI 图谱信息栏「IOF完备度 N%」+ 独立「本体完备度」导航页(KPI + BFO/成熟度分布条 + 缺口清单 + 一键升级)。
- [x] **一键升级**:`/api/ont/enrich`(补定义/反例)、`/api/ont/reground`(标注具体动词接地)、`/api/ont/maturity`(成熟度人审);`_ir_write_path` 守卫(只读源/穿越拒);图谱工具栏「补定义/反例」按钮 = enrich+reground。
- [x] **示例 升级实证**:15%→80%(108 对象补全定义/反例)→100%(60 关系接地);动词分布 归属25/描述13/服务7/产生4…,接地到 continuantPartOfAtAllTimes/describes/hasParticipantAtSomeTime 等。

## 任务 / Tasks
1. 注释字段先落 IR schema + prompt,再逐口(ir_to_graph / 对象详情 / 关系详情 / OWL / SPARQL / SHACL)透传,老 IR 优雅降级。
2. 三写端点复用 `_ir_write_path` + `_atomic_json` + `_WRITE_LOCK`,与 DR-006 写入原子性一致;引擎离线一律降级不编造。
3. 每端点补集成断言(maturity 非法/不存在/只读源/穿越;reground 只读源;forge SHACL conforms;completeness 结构)。

## 验收 / Acceptance
- 后端集成:新端点+回归 14 断言全通过;SHACL forge 对已补全 示例 返 `conforms`(4795 三元组);OWL/JSONLD/RDFXML 三格式带 108 条 naturalLanguageDefinition + 108 counterExample。
- 示例 完备度 100%(定义/反例/成熟度/BFO/接地各 108 或 60 满覆盖);OWL 导出、SPARQL 查 BFO 归类、UI 三页(图谱节点 IOF 卡片 + 成熟度 promote、完备度看板、深度问数)实测正常。
- 浏览器控制台零应用错误;服务日志零 500。
