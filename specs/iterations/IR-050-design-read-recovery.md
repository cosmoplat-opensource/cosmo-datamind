# IR-050 · 设计页读取失败后的有限重试与手动重新读取

状态：delivered。关联 [DR-189](../decisions/DR-189-design-read-recovery.md) 和[验收映射](../acceptance.json)中的 DESIGN-POLL-01。

## 验收

- [x] 运行中的设计读取连续失败时，按 2、4、5、5、5 秒最多自动重试 5 次，之后停止并等待手动重新读取。重新读取成功后恢复每秒轮询，失败计数清零。
- [x] 非暂时性读取错误不自动重试；首次打开失败时显示错误和重新读取入口。
- [x] 写请求超时提示结果待确认，不自动重发；重新读取只发送读取请求，再次提交沿用原请求 ID。
- [x] 离开页面后不再执行待定的重试。
- [x] 读取超时同样计入上限：首次超时后再自动重试 5 次，连续 6 次失败后停止自动读取。
- [x] 浏览器中首次读取被拒绝后显示“重新读取设计状态”，点击后加载设计，不产生写入。

## 定向验证

使用开发测试依赖、Node 22、SQLite 隔离存储、合成设计会话和 Chromium，在仓库根目录用 Bash 复现。控制器用例使用合成网络响应和可控定时器。

```bash
python -m pytest tests/unit/test_robotics_design_ui.py tests/unit/test_robotics_design_http.py \
  tests/unit/test_interactive_ontology_graph_network.py
python -m pytest tests/frontend/robotics-design-browser.py
python -m pytest tests/meta/test_acceptance_traceability.py tests/meta/test_doc_consistency.py \
  tests/meta/test_repository_materials.py tests/meta/test_public_content.py
```
