# IR-052 · 固定版本图谱的条件读取与分块完整性校验

状态：delivered。关联 [DR-191](../decisions/DR-191-fixed-graph-revalidation.md) 和[验收映射](../acceptance.json)中的 GRAPH-CACHE-01。

## 验收

- [x] 首次读取返回 200、带引号的 64 位十六进制 ETag 和 `Cache-Control: private, no-cache`；带匹配的 `If-None-Match`（包括多值列表）时返回空响应体的 304，且不读取登记文件、不生成图谱；不匹配时返回与首次相同的 200 响应。读取不产生写入。
- [x] 本体改名或图谱生成代码变化后，旧 ETag 不再匹配，返回新内容和新 ETag。
- [x] 带旧 ETag 的请求仍执行领域、版本和回收站检查：不存在的版本或其他领域返回 404，已删除本体返回 409；历史查看有独立 ETag，可再次得到 304。
- [x] 离线版本的来源文件按分块核对，不整份读取；本体载荷仍整份读取。来源被改动一个字节或被截断时返回 409 `artifact_integrity`。
- [x] 分块核对每次读取不超过设定块大小，摘要不符、长度不符和越界路径均被拒绝。
- [x] 真实浏览器再次打开同一图谱时发送 `If-None-Match`，服务端对该请求返回 304，图谱正常显示。

## 定向验证

使用开发测试依赖、Node 22、SQLite 隔离存储、合成离线构建和 Chromium，在仓库根目录用 Bash 复现。Chromium 把重新验证后的缓存命中报告为 200，因此浏览器用例检查实际发出的请求头，并用同一请求直接确认服务端返回 304。

```bash
python -m pytest tests/unit/test_interactive_ontology_graph_cache.py tests/unit/test_interactive_ontology_graph_http.py \
  tests/unit/test_robotics_offline_export.py tests/unit/test_interactive_ontology_graph_ui.py
python -m pytest tests/frontend/robotics-interactive-ontology-graph-browser.py
python -m pytest tests/meta/test_acceptance_traceability.py tests/meta/test_doc_consistency.py \
  tests/meta/test_repository_materials.py tests/meta/test_public_content.py
```
