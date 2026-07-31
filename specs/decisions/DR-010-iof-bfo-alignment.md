# DR-010 · IOF/BFO 本体工程对齐(注释词表 / 关系接地 / 注释化 OWL 导出 / 完备度门禁)

- **状态 / Status**: accepted
- **日期 / Date**: 2026-07-17
- **决策者 / Deciders**: Boss(用户) + Writer(Claude)
- **关联 / Refs**: 参照 github.com/iofoundry/ontology(Industrial Ontology Foundry,顶层 BFO 2020 + Core 中层 + `iof-av` 注释词表 + HermiT/SHACL 门禁);`server.py`(`_KIND_BFO`/`_FOUNDED_RELATIONS`/`_ground_verb`/`_ir_to_turtle`/`_IOF_SHACL`/`ont_completeness`/`ont_enrich`/`ont_reground`/`ont_maturity`)、`quick_build.py`、`ui/index.html`;衔接 [[DR-002-multimodal-llm-anti-fraud-build]]、[[DR-009-object-cn-display]]。

## 上下文 / Context
自研本体 IR 只有 `kind∈{object,event,asset,role}`+自由中文动词,缺理论根基、无机读定义/溯源、导出 OWL 无注释,无法与工业本体生态(IOF/BFO、Protégé/HermiT)互操作,也难以量化「可审计程度」。IOF 提供了成熟范式:BFO 上层归类、`iof-av` 机读注释(定义/示例/反例/成熟度/来源)、有根据关系 + 时间指标、SHACL 质量门禁。

## 决定 / Decision
在**不改平台代码**的前提下,给 DataMind 加一层 IOF 语义工程能力,六件事:
1. **BFO 上层归类**:`kind → _KIND_BFO`(object→MaterialEntity、event→Process、asset→MaterialArtifact、role→Role、ice→InformationContentEntity);节点/导出带 `bfo`,老 IR 由 kind 回溯。
2. **`iof-av` 机读注释**:对象带 `definition`(属+种差)、`isPrimitive`(无充要定义→标原始概念,诚实不编造)、`example`、`counterExample`(辨伪反例)、`maturity`(Provisional/Released/Deprecated)、`provenance{directSource,adaptedFrom,excerptedFrom}`。抽取 prompt(`_llm_extract_ontology`)加第⑥条定义纪律。
3. **关系接地**:`_FOUNDED_RELATIONS`+`_ground_verb()` 把中文动词→BFO 有根据关系(归属→continuantPartOfAtAllTimes、产生→hasSpecifiedOutput、服务→hasParticipantAtSomeTime…)+ 时间指标 `temporal`(atAllTimes/atSomeTime);未知动词回退 `relatedToAtSomeTime`。`ir_to_graph` 对老 IR 现场接地。
4. **注释化 OWL 导出**:`_ir_to_turtle` 重写为自包含 IOF 注释化 OWL2 生成器(BFO `subClassOf` + `iof-av:*` + 关系 `subPropertyOf` + 字段级 `DatatypeProperty`),ttl/jsonld/owl 三格式一致;`/api/sparql` 与 `/api/ont/forge`(SHACL)同底,注释可被 SPARQL 查询。**不改平台 `export_owl.py`**。
5. **SHACL 质量门禁**:`_IOF_SHACL` 形状(类须有 `rdfs:label`;非原始类须有 `naturalLanguageDefinition`),`/api/ont/forge` 用 pyshacl 校验、返回 conforms/violations。
6. **完备度记分卡 + 一键升级**:`/api/ont/completeness/<key>`(定义/反例/成熟度/BFO/接地加权分 + 缺口清单);`/api/ont/enrich`(LLM 补定义/反例回写)、`/api/ont/reground`(标注具体动词接地)、`/api/ont/maturity`(成熟度人审)——三写端点均经 `_ir_write_path` 只允许 imom/app/built/forged 的 JSON 回写,拒只读平台源(cq 的 .js)与路径穿越;引擎离线不臆造。

## 后果 / Consequences
- (+) 图谱可与 IOF/BFO 生态互操作;导出 OWL 带机读定义/反例/成熟度/溯源,可进 Protégé/HermiT/SHACL。
- (+) 反造假(DR-002)升级为「数据裁决 + IOF 定义纪律」:能证则证(verified)、能定义则定义、否则诚实标 candidate/原始概念。
- (+) 完备度可量化;真实 示例 图谱经一键升级达 100%(108 对象定义/反例全、60 关系接地全)——为专利/答辩提供「可审计本体」实证。
- (+) 三写端点与 [[DR-001]] 只读执行正交:只写本系统 workdir/forged 下的 IR JSON,不碰数据库。
- (−) 定义/接地依赖在线引擎(离线只降级不编造);数据驱动兜底(quick_build)对象标原始概念、关系接地为 relatedTo(诚实)。
- (−) `iof-av`/BFO IRI 采用 IOF 官方命名空间但未 import 其 OWL 本体(仅注释对齐);如需严格 import 需另核许可与 catalog。
