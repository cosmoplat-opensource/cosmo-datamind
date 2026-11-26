# IR-053 · 图谱副本的目录当前版本与生成边界

状态：delivered。关联 [DR-192](../decisions/DR-192-graph-copy-boundaries.md) 和[验收映射](../acceptance.json)中的 GRAPH-COPY-01。

## 验收

- [x] 保存已发布版本的图谱副本后，目录卡片仍指向原版本，版本号和发布状态不变，版本列表包含副本。之后登记的普通版本成为当前版本。
- [x] 应用版本的副本及其再次保存的副本在应用检查中准备状态为 blocked，结构层给出 `schema.application_copy` 提示，生成前的就绪校验拒绝。原应用版本和设计版本的副本不受此规则影响。

## 定向验证

使用开发测试依赖、SQLite 隔离存储和合成本体，在仓库根目录使用 Bash 复现。

```bash
python -m pytest tests/unit/test_graph_copy_boundaries.py tests/unit/test_graph_copy_version.py \
  tests/unit/test_ontology_asset_catalog.py tests/unit/test_application_contract_{service,freshness}.py \
  tests/unit/test_robotics_application_{service,artifacts,http}.py tests/unit/test_asset_governance.py
python -m pytest tests/frontend/robotics-interactive-ontology-graph-browser.py \
  -k 'catalog_lists_multiple_versions_without_graph_edits or save_edit_cursor_retry_and_reopen_mapped_version'
python -m pytest tests/meta/test_acceptance_traceability.py tests/meta/test_doc_consistency.py \
  tests/meta/test_repository_materials.py tests/meta/test_public_content.py tests/meta/test_architecture.py
```
