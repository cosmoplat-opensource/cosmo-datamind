# IR-055 · 公开文档不以本地文件作为证据

状态：delivered。关联 [DR-194](../decisions/DR-194-local-evidence-citations.md) 和[验收映射](../acceptance.json)中的 REPO-EVIDENCE-01。

## 验收

- [x] 新增规约检查在修改前报出 IR-016 的 2 处、IR-017 的 1 处和 DR-022 的 2 处本地文件路径，修改后通过；只描述材料边界的目录名不受影响。
- [x] IR-016 的兼容基线和验证记录、IR-017 的验证记录改为说明验证内容、对应验收和复现命令，并注明原始记录仅保留本地。
- [x] 改写后引用的单元测试、机器人离线演示和存储离线演示（含独立进程核验）在当前版本实际执行通过。

## 定向验证

使用开发测试依赖，在仓库根目录用 Bash 复现。

```bash
python -m pytest tests/meta/
python -m pytest tests/unit/test_robotics_baseline.py tests/unit/test_robotics_imports.py \
  tests/unit/test_ontology_asset_catalog.py tests/unit/test_ontology_asset_versions.py \
  tests/unit/test_legacy_asset_adapter.py tests/unit/test_domain_run_store.py tests/unit/test_domain_artifacts.py \
  tests/unit/test_domain_storage_integrity.py tests/unit/test_domain_storage_demo_cli.py
python -m pytest tests/unit/test_robotics_*.py
python scripts/demo_robotics.py --case all --output-dir .check-results/robotics-demo
python scripts/demo_domain_storage.py --case all --output-dir .check-results/domain-storage-demo
```
