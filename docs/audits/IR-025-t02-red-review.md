# IR-025 T02 RED 测试评审 — DR-071

日期：2026-11-12。
状态：测试已编写并运行，待人工审阅覆盖；尚未进入 T03 实现。
依据：[冻结契约](../../specs/contracts/application-contracts-v1.md)、[IR-025](../../specs/iterations/IR-025-application-contract-registry.md)。
结果：[结构化记录](IR-025-t02-red-results.json)。

## 结果

37 个测试函数经参数化展开为 80 项：78 项预期失败，2 项通过，0 收集错误，0 跳过。
78 项失败全部是目标包 `application_contracts` 尚未实现，缺少能力在测试函数执行时暴露。
没有使用 skip、xfail、assert False 或伪造实现制造 RED。
当前尚未执行到这 78 项的业务断言；这些断言是否正确完整，仍需代码审阅及后续 GREEN 阶段验证。

## 测试范围

| 文件 | 项数 | 关注点 |
|---|---:|---|
| [注册表](../../tests/unit/test_application_contract_registry.py) | 13 | 固定入口绑定、多应用、重复登记、未知入口、适配器不可用、注册描述不能被外部修改 |
| [分层检查](../../tests/unit/test_application_contract_checks.py) | 38 | 无任务、必需／可选项、六种状态、依赖检查、缺项证据、未实现修复、异常、漏项和重复结果、快照不被修改、引用与模式边界 |
| [新鲜性与执行守卫](../../tests/unit/test_application_contract_freshness.py) | 27 | 实际内容摘要、完整有序版本向量、任务／环境／策略变化、过期和跨应用结果、伪造 ready、执行前重新读取和检查 |
| [现有构建基线](../../tests/unit/test_application_contract_build_baseline.py) | 2 | 旧离线构建直接保存；新 TBox＋ABox 候选虽不满足 UR 消费要求仍能保存和只读查看 |

共用 [测试夹具](../../tests/application_contract_support.py) 只提供输入、记录调用和合成检查结果，不实现状态聚合、摘要比较或授权逻辑。
新核心测试的 passed 仅表示合成检查器返回 passed，不证明真实机器人适配或深度问数支持。
两条构建基线使用真实 SQLite、构建管线和资产读取；模型路径用确定性本地响应，禁止网络，不调用外部模型。
所有写入发生在 pytest 隔离临时目录，不触碰用户当前本体和数据库。

## 本轮未覆盖的部分

- 浏览目录／详情／图谱时不发起契约检查，以及按钮可进入诊断：T04 HTTP／浏览器测试。
- 报告持久化、幂等并发、事务失败、重启读取及修复分发：T04 集成测试。
- 精细 TBox/ABox 缺项与实际补全、新旧生成对照：IR-026。
- 真实数据问数、连接与 edits 解析、多图 SQL 和缓存：IR-027。
- 删除竞态和完整详情视觉：IR-028／029。

因此 ACR-01～04 都只有部分测试覆盖；实际函数引用已加入 acceptance.json，不标记验收完成。
IR-024 旧 compatibility／按钮测试尚未修改，按 T01 约定留待 T04 RED 明确评审。

## 规约与静态检查

新增测试及夹具的 ruff 检查通过；验收引用自检 1 项通过；29 个本地文档链接有效；git diff --check 通过。
这些检查验证文件和追溯关系，不代表新增应用功能已经通过。

## 人审后下一步

确认测试覆盖后进入 T03 GREEN，实现公共注册、分层检查、报告基础和新鲜性守卫，让已审测试转绿。
T03 不修改本轮测试来放宽断言；若发现规约或测试缺陷，先明确说明，再按用户确认的范围处理。
