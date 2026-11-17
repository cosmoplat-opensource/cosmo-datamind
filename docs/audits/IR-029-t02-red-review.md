# IR-029 T02 RED · 本体删除恢复与历史保留

日期：2026-11-16。关联 DR-075。
用户要求继续 IR029，本次完成 T01 规约和 T02 RED；没有实现 T03，也未启用任何删除／恢复入口。

## 可审阅结果

- [T01 生命周期规约](../../specs/contracts/ontology-asset-lifecycle-v1.md)。
- [52 项 RED 测试](../../tests/unit/test_asset_lifecycle.py)。
- [机器结果和冻结摘要](IR-029-t02-results.json)。

运行：`.venv/bin/python -m pytest tests/unit/test_asset_lifecycle.py -q --junitxml=.check-results/ir029-t02/red-final.xml`。
结果：52 项失败、0 环境错误、0 跳过；全部失败指向尚未实现的 `ontology_assets.lifecycle` 服务。
通过测试函数内的明确断言报告缺失实现，不以 collection error 或夹具异常冒充 RED。
夹具使用临时 SQLite、真实不可变文件及最小 JSON／RDF 候选，没有模型或用户资产删除。
首版 45 项 RED 也保留；补充并发幂等、无效 ID 及未知资产后形成最终 52 项基线，没有通过放宽断言制造通过。

## 覆盖

| 验收 | 本阶段覆盖 | 后续边界 |
| --- | --- | --- |
| OAL-01 | 数据／机器人删除恢复、目录默认过滤、全部／回收列表、只读预览 | 真实 HTTP 和目录 UI 留 T04／T05 |
| OAL-02 | 原身份／版本／来源／发布元数据／文件保留、共享来源计数、历史运行 manifest 和下载不变 | 真实设计／任务历史及工作副本迁移留后续 |
| OAL-03 | 修订和影响冲突、幂等重放、恢复后旧删除重放、并发同请求和竞争请求、事务回滚、旧 key 快照和 tombstone | 排队受理竞态、等待输入冻结、旧 HTTP 删除、扫描／缓存／重启留 T04 |
| OAL-04 | 明确 confirmed=True、拒绝 truthy 替代、服务器保护持久化、demo／app 身份保护、名称不能冒充内置身份 | 页面确认和恢复交互留 T04／T05 |

## 需要审核的规则

1. 删除整份资产，全部版本和历史仍保留；恢复原 ID／版本，生命周期修订递增，旧 ready 不自动恢复。
2. 预览绑定 revision 和完整引用影响摘要；确认后才能提交，依赖变化要求刷新。
3. queued／running 有控制依赖时拒绝删除；等待输入会话可随资产进入冻结状态，恢复后复检。
4. 共享来源保留；历史读取不能被执行入口的删除守卫误挡。
5. 旧 key 有快照才可恢复；过去已物理删除且无快照的 key 保留持久删除记录，不能伪造可恢复资产。
6. 成功幂等重放返回原 operation 加当前 lifecycle；恢复后重放旧删除请求不再次删除资产。

本轮未继承 IR028 的 T02／T04 免等待授权；该授权针对 IR028。
依据 [sdd-workflow 的 SKILL.md](../../.agents/skills/sdd-workflow/SKILL.md) 第⑤环节“**人 review 测试是否覆盖到位**”，现在提交具体规约和测试供审核；通过后 T03 只能修改实现使这些测试转绿。
尚未运行以 GREEN 为前提的全量回归，不宣称业务验收通过，不修改用户数据库、工作文件或正在运行的服务。
