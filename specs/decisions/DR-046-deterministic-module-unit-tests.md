# DR-046 · 确定性模块的隔离单测

- **状态 / Status**: accepted
- **日期 / Date**: 2026-07-31
- **决策者 / Deciders**: Boss(用户) + Writer(Claude)
- **关联 / Refs**: [[IR-007-tdd-foundation]]、[[DR-045-engineering-harness-and-tdd]];实证=`tests/unit/*`、`tests/conftest.py`

## 上下文 / Context

11 个确定性无 LLM 模块(`health_check`/`cq_check`/`drift_check`/`intent_check`/`rule_engine`/
`module_split`/`compat_check`/`usage_stat`/`quick_build`/`openai_runtime`/`translate_cn`)是本系统
最健康的部分——各 95–299 行、纯函数为主、可独立测。但此前它们**只经 HTTP 层间接触达**
(在 `test_all.py` 各 DR 小节里对活服务的 IR 断言),边界从未被隔离测过:
空图、环、复合键、状态矛盾、CTE 混入等边角一旦回归,集成套件未必覆盖到。
且 `quick_build.py` 在 import 时解析 `sys.argv` 并连库,**根本无法被 import**,纯函数不可测。

## 决定 / Decision

1. **为确定性模块建离线单测**(`tests/unit/`),用手搓夹具(最小 IR / 规则集 / 临时 SQLite)直接调纯函数,不起服务。
2. **`quick_build` 可测化**:把 CLI/构建逻辑收进 `build()` + `if __name__=="__main__"` 守卫,
   纯函数(`key_stem`/`key_name_ok`)与 `build()` 均可编程调用;**server 子进程 CLI 行为逐值不变**。
3. **锁现状即锁盲区**:对已知算法盲区(自引用键 `ReportsTo` 发现不了)写「现状断言」并注明
   「DR-036 落地后反转」——单测同时是回归锁与改进路标。
4. **覆盖率只统计确定性模块**:`server.py` 由集成套件覆盖,混入单计会失真。

## 后果 / Consequences

- (+) 43 条离线单测(秒级),补齐审计点名的「无真单测」空白;确定性模块 72.0% 覆盖率基线。
- (+) `quick_build` 从不可 import 变可测,且 `build()` 可编程复用。
- (+) 单测即刻产出价值:red→green 过程验证 `quick_build` 重构无回归;`intent_check` CTE 剔除等边界被锁。
- (−) LLM 分支仍未单测(离线降级下运行),留待引入 mock 层(后续 IR)。
