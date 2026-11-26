# IR-052 · 图谱编辑副本保存为新版本

状态：delivered。关联 [DR-191](../decisions/DR-191-graph-copy-version.md) 和[验收映射](../acceptance.json)中的 GRAPH-SAVE-01。

## 验收

- [x] 保存完整图谱并生成可重开的候选子版本，原版本可继续加载，其发布状态、评审工作头和已有应用检查的有效性保持不变，进行中的适配可继续提交。
- [x] 基于同一父版本和同一组操作的并发保存及重试返回同一版本。空操作、摘要冲突、无效编辑和写入受限时不会产生新版本。
- [x] 编辑器保存当前撤销位置的结果，失败可重试，成功后打开新固定版本，刷新后可见编辑结果。应用图中有效的类别映射继续生效。
- [x] 活动机器人本体从一个登记版本增至两个时，目录显示「版本列表」。即使所有版本均由其他流程产生，也能从列表打开早期版本。桌面和窄屏下打开列表时卡片高度保持稳定，长列表可在弹窗内滚动。

## 定向验证

使用开发测试依赖、SQLite 隔离存储、合成本体和 Chromium。接口用例从已发布版本生成子版本，比较父版本原始字节、发布记录及新版本的完整 RDF。设计版本用例在应用检查及适配开始后保存，再读取检查报告并继续提交适配。浏览器用例在服务端完成写入后中断响应，重试并刷新候选图谱，再通过本体目录的版本列表打开原版本。

首次交付的定向回归覆盖 335 项功能与接口用例、22 项 Chromium 用例及 56 项规约与仓库检查，均已通过。JSON-LD 回读用例有一条 RDFLib 弃用提示。验证范围为图谱保存及关联的版本存储、目录、适配、应用检查与评审。

在仓库根目录使用 Bash 复现本轮定向检查。

```bash
python -m pytest tests/unit/test_graph_{copy_version,edit_copy,relation_endpoints,directional_relations}.py \
  tests/unit/test_mapped_graph_classes.py tests/unit/test_interactive_ontology_graph_{http,ui}.py \
  tests/unit/test_ontology_asset_{versions,catalog}.py tests/unit/test_ontology_detail_ui.py \
  tests/unit/test_asset_governance.py tests/unit/test_robotics_{review_http,application_service,application_artifacts,application_http}.py \
  tests/unit/test_application_contract_{service,freshness}.py
python tests/frontend/robotics-interactive-ontology-graph-browser.py
python -m pytest tests/meta/test_{acceptance_traceability,repository_materials,public_content,doc_consistency,architecture}.py
```

版本列表显示条件调整后，以下 3 项浏览器用例及 54 项界面、验收追踪与仓库材料检查通过。浏览器覆盖单版本、多版本且没有手动编辑记录、保存过编辑副本和回收站场景。

```bash
python -m pytest tests/frontend/robotics-interactive-ontology-graph-browser.py \
  -k 'catalog_lists_multiple_versions_without_graph_edits or save_edit_cursor_retry_and_reopen_mapped_version or recycle_bin_graph_round_trip_preserves_fixed_version'
python -m pytest tests/unit/test_interactive_ontology_graph_ui.py \
  tests/meta/test_acceptance_traceability.py tests/meta/test_repository_materials.py
```

版本列表改为弹窗后，重跑上述两条命令，3 项浏览器用例及 54 项界面和规约检查通过。目录用例使用 12 个版本，在 1280 和 390 像素宽的视口比较打开前后的卡片高度，核对列表可滚动、关闭后焦点返回入口，并打开最早版本。
