# IR-038 · 图谱故障时的设计操作回执

状态：delivered。关联 [DR-177](../decisions/DR-177-design-operation-receipt.md) 和 DESIGN-RECEIPT-01。

## 验收

- 确认类别和提交消息分别按同步保存和异步受理返回。图谱故障可观测，响应保留真实业务状态。
- 同一请求重复提交复用原修订或运行。冲突请求仍报告失败。重新读取和图谱恢复不产生额外写入。
- 页面显示已保存的修订及独立的图谱提示。恢复图谱后可以继续浏览。

## 复现

在[项目测试环境](../../README.md#测试)中执行以下相关回归。浏览器故障用例让确认请求经同一 Flask 路由写入临时数据库，仅在测试进程中替换图谱生成函数。刷新由正常服务读取已保存的修订。

```sh
python -m pytest -q -n 2 --dist=loadfile tests/unit/test_robotics_design_*.py \
  tests/unit/test_interactive_ontology_graph*.py tests/meta/test_acceptance_traceability.py
python tests/frontend/robotics-design-browser.py
python tests/frontend/robotics-interactive-ontology-graph-browser.py
```
