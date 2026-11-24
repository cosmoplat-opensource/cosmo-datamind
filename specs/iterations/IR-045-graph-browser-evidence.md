# IR-045 · 图谱浏览器测试证据

状态：delivered。关联 [DR-179](../decisions/DR-179-graph-browser-evidence.md) 和 [GRAPH-BROWSER-EVIDENCE-01](../acceptance.json)。

## 验收

- [x] 当前运行通过的用例保留浏览器记录并标记完成。断言失败、页面异常、准备或清理失败、跳过的用例均标记为未完成。
- [x] 同一目录重跑时，本轮失败或跳过的用例不能沿用旧的完成标记。
- [x] 套件入口逐项核对 JUnit 与证据。缺失、损坏和相互矛盾的记录使检查失败，pytest 的失败状态可传递至调用方。

本次定向回归中，JUnit 的 15 个图谱浏览器用例全部通过，逐项对应 15 份标记完成的证据，套件退出码为 0。失败与跳过路径使用隔离的 pytest 用例复现。

## 复现

在[项目测试环境](../../README.md#测试)中运行：

```sh
python -m pytest -q tests/unit/test_graph_browser_evidence.py
python tests/frontend/robotics-interactive-ontology-graph-browser.py --output .check-results/graph-browser-evidence
```

第二条命令生成 `results.xml`、各用例目录中的 `evidence.json` 和记录最终退出状态的 `runner.json`。
