# DR-161 · 显示范围、提交范围与保存范围一致

状态：accepted。日期：2026-11-23。依据：操作范围与结果完整性测评（本地审计材料，按 DR-160 不入库）确认的 11 组问题，验收标准 SCOPE-* 见 `specs/acceptance.json`。

## 决定

- **写入对象以页面显示为准。** 评审台连续切换图谱时只采用最后一次请求的结果，晚到的响应丢弃；提交审核时使用当前渲染数据所属的图谱。
- **每个 API 源独占一张物化表。** 表名仍为 `api_<连接名>`；连接名转写为空（如中文名）或与其他 API 连接撞名时，追加连接编号的摘要。取数时记下的表名只在不与其他连接共用时沿用。就绪状态按本连接的表判定。
- **未了结记录不因日志变长而删除。** 动作日志保留全部待审批与执行中记录，已了结记录按新旧顺序保留至 1,000 条；动作记录接口返回全部未了结记录与最近 200 条已了结记录，并给出总数。
- **深度问数的范围选择按确认结果发送。** 确认“不指定表”即清空逐表选择，请求不再携带先前的表名。
- **有上限的结果如实报告范围。** API 导入仍限 5,000 行、40 列，超出时返回 `truncated`、原始行数与列数，页面提示未导入部分；SPARQL 最多返回 500 行，`total` 为完整结果数并标记 `truncated`；问数历史回放保存至多 2,000 行，并记下原始行数，超出时回放卡注明。
- **导出与筛选一致。** 动作中心导出 CSV 使用与列表相同的筛选，无匹配时按钮不可用；CSV 表头改用半角逗号分隔。
- **“全选本页”只看本页显示的行。** 取消全选只取消本页行。
- **观测事件先过滤再取最近若干条。** 不再先截取文件末尾 5,000 行再按类型过滤。

## 验收与恢复

需求 SCOPE-REVIEW-01/02、SCOPE-SOURCE-01、SCOPE-RETENTION-01、SCOPE-PENDING-01、SCOPE-IMPORT-01、SCOPE-CHAT-01、SCOPE-REPLAY-01、SCOPE-RDF-01、SCOPE-CSV-01、SCOPE-OBS-01 对应 `tests/unit/test_result_scope.py` 与 `tests/frontend/result_scope_browser.py`，每项都有边界内的对照用例。

复现：`python -m pytest tests/unit/test_result_scope.py tests/frontend/result_scope_browser.py`，以及 `python scripts/check.py --suite unit`。

兼容性：已物化且未与其他连接共用表名的 API 源沿用原表；曾与其他连接共用一张表的 API 源变为未就绪，重新取数后写入各自的表。接口新增字段（`truncated`、`total_rows`、`total_columns`、动作记录的 `total`）只增不改。回退时撤销本次提交即可，不涉及数据库结构迁移。
