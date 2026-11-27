# DR-188 · 读取设计时图谱故障按展示提示处理

状态：accepted。
实现交付状态：delivered。

## 上下文

[DR-177](DR-177-design-operation-receipt.md) 让设计写操作在图谱生成失败时仍返回已保存的回执，并附带 `interactive_graph_warning`，提示用户刷新页面重新读取。但读取会话（`GET /api/robotics/design-sessions/<sid>`）和读取历史修订 JSON（`GET .../revisions/<revision>`）仍把图谱生成放在设计文件完整性检查内。图谱生成一旦失败，两个读取接口都返回 HTTP 409 `artifact_integrity`，提示“设计文件缺失或损坏”。

如果故障持续存在，用户按提示刷新后仍然读不到设计。排队或运行中的设计在轮询读取时也会收到 409，页面停在错误状态。此时设计文件本身完好，错误提示与事实不符。

## 决定

- 读取会话和读取历史修订 JSON 时，图谱生成失败只影响图谱展示：返回 HTTP 200 和已保存的设计内容，`interactive_graph_view` 为 `null`，`interactive_graph_warning` 使用固定错误码 `graph_view_unavailable`，提示“当前修订的图谱暂时无法显示，设计内容不受影响。可稍后刷新重试。”写操作的回执沿用 DR-177 的提示。
- 读写两条路径使用同一个降级函数。故障计入指标 `robotics.design.graph_projection_failed`：读取记为 `operation=read`，写操作记为对应操作名。日志只记录异常类型，不记录异常内容。
- 设计文件本身缺失、被篡改或无法解析时，仍返回 409 `artifact_integrity`。
- 读取不产生写入。图谱恢复后，同一读取请求重新返回图谱，不再带提示。

## 关联

[IR-049 迭代与复现](../iterations/IR-049-design-graph-read-degradation.md) ／ [验收映射](../acceptance.json)中的 DESIGN-RECEIPT-02。
