# IR-039 · 历史设计修订的写操作保护

状态：delivered。关联 [DR-178](../decisions/DR-178-design-history-read-only.md) 和 DESIGN-HISTORY-01。

## 验收

- 在失败或中断的当前设计中选择历史修订，读取期间和读取完成后均拦截设计写操作。
- 从历史设计切回当前设计时保留草稿，浏览过程不增加运行、修订或模型调用。
- 切回后显式重试一次，服务端使用当前修订和对应的失败运行创建一次重试。

## 复现

在[项目测试环境](../../README.md#测试)中执行以下回归。控制器测试需要 Node 在命令搜索路径中。

```sh
python -m pytest -q tests/unit/test_robotics_design_ui.py tests/unit/test_robotics_design_service.py tests/unit/test_robotics_design_http.py
python tests/frontend/robotics-design-browser.py
python tests/frontend/robotics-interactive-ontology-graph-browser.py
```
