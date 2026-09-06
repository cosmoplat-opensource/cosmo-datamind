# Apache Ossie (incubating) 机器可读 schema

- 来源:https://github.com/apache/ossie(原 Open Semantic Interchange / OSI)
- 快照 revision:b7404027d2676361b128921a64c7f2592bac404d
- 快照日期:2026-09-06
- 文件:
  - `ossie-schema.json` — 核心语义模型 schema(`core-spec/ossie-schema.json`,version const `0.2.0.dev0`)
  - `ontology-schema.json` — 本体规范 schema(`ontology/ontology.json`,version const `0.2.0.dev0`)
- 许可:Apache License 2.0(见同目录 LICENSE / NOTICE)
- 用途:`osi_export.py` 的导出形状以这两份 schema 为准;`scripts/validate_ossie_export.py`
  在本机装有 jsonschema/pyyaml 时按官方 validator 的同一 schema 做校验。
- 注意:`ontology-schema.json` 的 `OntologyMap.semantic_model` 以远程 $ref 指向核心 schema;
  本仓导出的本体文档不含 `ontology_mappings`,校验时不会触发远程解析。
