# IR-031 T01 · 契约冻结记录 — DR-159

日期：2026-11-17。
分支：`feat/studio-ui-integration`。

用户授权按 SDD 启动 IR-031，每个 Task 完成相关回归后提交。
T01 冻结 `specs/contracts/studio-graph-view-v1.md`，包含投影与组件接口、来源身份、确认衔接、完整 IRI、并集语义、实例口径、分批与错误，以及实现前固定的性能判据。
源码来源的 8 份文件摘要与实际附件逐项一致，清单位于 `specs/sources/IR-031-studio-sources.json`。
相关文档链接检查与 `git diff --check` 通过。

执行 `.venv/bin/python -m pytest tests/meta/test_acceptance_traceability.py tests/unit/test_robotics_graph_views.py tests/unit/test_robotics_design_assets.py -q`，退出 0，16 项通过。
这证明当前追溯映射、原关系图投影和设计候选读取基线仍通过，不证明新组件已实现或性能已达标。
Node 使用 Playwright 随包 Node 24.18.1；没有安装新运行时或修改系统 PATH。
T01 提交包含此前已编写但未提交的 DR-159、IR-031／032、计划验收映射与索引，作为本次实现的规约基线。
T02 行为测试与产品实现不包含在 T01 提交中。
