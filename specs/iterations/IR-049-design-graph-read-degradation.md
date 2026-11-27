# IR-049 · 读取设计时图谱故障按展示提示处理

状态：delivered。关联 [DR-188](../decisions/DR-188-design-graph-read-degradation.md) 和[验收映射](../acceptance.json)中的 DESIGN-RECEIPT-02。

## 验收

- [x] 图谱持续无法生成时，读取会话和历史修订 JSON 返回 HTTP 200 和已保存的设计内容，图谱字段为空并附带读取提示，响应和日志不含异常内容，故障按读取计数，数据库与文件不变。
- [x] 写操作降级后刷新读取，仍能看到已保存的修订。
- [x] 页面加载、刷新和切换历史修订时显示图谱提示，不显示“设计文件缺失或损坏”，确认操作仍可使用。
- [x] 修订文件被篡改时，导出仍返回 409 `artifact_integrity`。

## 定向验证

使用开发测试依赖、SQLite 隔离存储、合成设计会话和 Chromium，在仓库根目录用 Bash 复现。浏览器用例把设计读取请求转到同一 Flask 路由和数据库，并在测试进程内持续替换图谱生成函数。

```bash
python -m pytest tests/unit/test_robotics_design_http.py
python -m pytest tests/frontend/robotics-design-browser.py
python -m pytest tests/meta/test_acceptance_traceability.py tests/meta/test_doc_consistency.py \
  tests/meta/test_repository_materials.py tests/meta/test_public_content.py
```
