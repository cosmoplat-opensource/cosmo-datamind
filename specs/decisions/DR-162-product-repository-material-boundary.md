# DR-162 · 产品仓库与本地开发材料边界

状态：accepted。日期：2026-11-18。依据：维护者明确要求保留决策与迭代记录，其余指定开发中间材料及 `specs/contracts/` 不再提交。

## 决定

- 审查报告、逐阶段运行结果、日志截图、探索方案、来源迁移快照、评审方案、界面原型及一次性过程说明保留本地副本，退出 Git 跟踪。
- `specs/decisions/`、`specs/iterations/`、现有产品指南、有效行规则及流水线说明继续保留。需求与测试追溯仍由 `specs/acceptance.json` 维护。
- 忽略规则与元测试共同约束提交范围；保留文件不得继续链接到未发布的本地材料。
- 兼容基线直接重建并比较输入结构、检查夹具未变、执行消费者接受/拒绝矩阵与严格生成失败路径，不再读取历史运行报告。跨目录和导入顺序继续由既有隔离子进程测试覆盖。
- 本次仅以普通提交更新远端分支当前目录树，不改写历史提交；已经发布的旧版本仍能从 Git 历史访问。

## 验收与恢复

需求 `REPO-MATERIAL-01` 对应 `tests/meta/test_repository_materials.py`，兼容行为对应 `tests/unit/test_robotics_baseline.py` 与 `tests/unit/test_robotics_imports.py`。

复现：安装开发依赖后执行 `python -m pytest tests/meta/test_repository_materials.py tests/unit/test_robotics_baseline.py tests/unit/test_robotics_imports.py -ra`，以及 `python scripts/check.py --suite unit`。运行前记录 `git rev-parse HEAD`、`git status --short` 和依赖版本；新克隆也必须通过，不依赖被忽略的本地副本。

需要恢复跟踪时，由维护者撤销本次普通提交并重新评审提交边界。本地文件不因退出跟踪而删除；本次不改变产品 API、存量资产或数据库结构。
