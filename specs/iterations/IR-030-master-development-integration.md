# IR-030 · master 与机器人功能的兼容集成

2026-11-17，机器人功能经 PR #1 合入 master（`a1d16b6`）。合入前未进行人工代码审查。合入后的审查与修复见 [DR-159](../decisions/DR-159-pr1-review-fixes.md)。

## 范围与验收

集成决定见 [DR-158](../decisions/DR-158-master-development-integration.md)。数据源隔离、发布规则、缓存、删除保护、反馈回放及 SQL 修正须与机器人双路径构建、任务生成、固定版本、目录及回收站共存。集成时保留双方的覆盖率和类型检查门槛，并增加了生命周期与问数的交叉回归。

## 历史验证与复现

集成版本 `d735e4c` 记录了合入 PR 前的 16 段隔离验证。单元测试 3744 项通过，5 项可选上游引擎检查跳过。联合场景 55/55 通过，HTTP 集成 536 项通过、6 项条件跳过。该次运行的原始日志仅内部留存。测试环境为 Python 3.10、隔离数据库及资产库、浏览器和受控模型边界。该记录只覆盖当时的集成范围，外部模型和真实机器人设备未参与验证。

在[项目测试环境](../../README.md#测试)中，可以使用仓库内的合成数据复查生命周期、问数和场景兼容性。以下命令会在读者自己的检出版本上生成新的结果。

```sh
python -m pytest -q tests/unit/test_merge_lifecycle_regressions.py tests/unit/test_merge_qa_regressions.py
python scripts/scenario_bench.py
```

## 运行环境边界

Python 3.10 不提供 SQLite `Connection.setlimit`；本次增加显式 `zeroblob/randomblob` 分配前限额，并保留查询超时与返回截断。
这不等价于 Python 3.11 及以上的统一 SQLite 单值引擎上限，生产环境若要求该资源保护完整生效，应使用 Python 3.11 及以上。
业务概念取证后的 authorizer 清理也已适配 Python 3.10，避免后续合法表结构读取被误拒绝。
