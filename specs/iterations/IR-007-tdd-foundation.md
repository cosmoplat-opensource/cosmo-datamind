# IR-007 · 工程校验与 TDD 底座

- **状态 / Status**: in-progress
- **日期 / Date**: 2026-07-31
- **关联 / Refs**: [[PROPOSAL-sdd-tdd-improvement]];新增 DR-045(工程校验)、DR-046(确定性模块单测);
  实证=`pyproject.toml`、`tests/`、`requirements-dev.txt`、`.github/workflows/ci.yml`、`.pre-commit-config.yaml`

## 目标 Goal

把「测试写在实现之后、校验靠手动」升级为**测试先行 + 合并即挡**的 TDD 底座。
现状:535 条 `chk()` 是集成级表征锁(需起服务),11 个确定性模块只经 HTTP 间接触达,
无 CI / lint / coverage / 固定依赖。本迭代补齐**可离线秒级运行的单测层**与**工程校验**,
并确立一条**已知绿基线**,供后续算法迭代(IR-008)做红-绿。

## 交付 Deliverables

- [x] `pyproject.toml`:pytest 发现(pythonpath/testpaths)+ coverage(确定性模块)+ ruff(只选 F/B 真 bug 规则,避开与紧凑单行风格冲突的 E7xx)+ mypy。
- [x] `tests/` 单测层(离线、秒级、不起服务):
  - [x] `tests/unit/test_quick_build.py` —— **红→绿示范**:import 时解析 argv 致不可测(红)→ 收进 `build()`+`__main__` 守卫(绿)。
  - [x] `tests/unit/test_health_check.py`(孤岛/悬空/自反/状态矛盾/空图)
  - [x] `tests/unit/test_rule_engine.py`(校验/求值/追溯/冲突/一致性矛盾)
  - [x] `tests/unit/test_intent_check.py`(aligned/partial/mismatch/unknown/CTE 剔除)
  - [x] `tests/unit/test_cq_check.py`(answerable/partial/unanswerable)
  - [x] `tests/unit/test_drift_check.py`(表缺失检出/一致时不误报)
  - [x] `tests/unit/test_compat_module_usage.py`(兼容 diff/破坏性;模块化;使用度覆盖率)
- [x] `tests/meta/test_doc_consistency.py`:路由数(121)/断言数(535)对齐规范值,抓文档漂移。
- [x] `requirements-dev.txt`:固定版本的测试工具链 + 运行时可复现下界。
- [x] `.github/workflows/ci.yml`:unit job(单测+覆盖率+lint+mypy 硬挡)+ integration job(108表验证库，确定性失败硬挡)。
- [x] `.pre-commit-config.yaml`:提交即跑 ruff + 单测 + mypy。
- [x] `fail_under` 覆盖率门槛:81.0%；2026-08-29 实测 81.6%。

## 任务 Tasks(每项一次提交)

1. 建 `tests/` 脚手架 + `conftest.py`(IR/规则/临时 SQLite 夹具)。
2. 写 `test_quick_build.py`(先红)→ 重构 `quick_build.py` 加 `build()`+`__main__` 守卫(转绿);验证 server 子进程 CLI 行为不变。
3. 补 8 个确定性模块的隔离单测。
4. `pyproject.toml` 校验配置;ruff 由「全规则 1218 噪声」收敛为「F+B 25 条真 bug 信号」。
5. `test_doc_consistency.py` 抓到 ARCHITECTURE.md 119/531 漂移 → 订正为 121/535(测试转绿)。
6. `requirements-dev.txt` + CI + pre-commit。

## 验收 Acceptance

- [x] `pytest tests/` 全绿:**43 passed**(单测 40 + 元测试 3)。
- [x] `coverage report`:确定性模块 **72.0%** 基线(此前无覆盖率度量)。
- [x] `ruff check .`:F/B 规则 **25 条**真 bug 类 findings(F601 重复键 13 条经核验值相同无害;B023 闭包捕获 4 条列为 IR-008 前置排查项)。
- [x] `test_quick_build.py` 的 git diff 可证「先红(IndexError)后绿(5 passed)」。
- [x] 重构后 `quick_build.py` 子进程 CLI 产物与改造前逐值一致(orders→customers verified,overlap 100)。
- [ ] CI 在远端跑通(本仓当前未配远端；本地按相同命令验证，待推送后确认运行记录)。

## 备注

- ruff 刻意只选 F/B:E7xx/E4xx 与本库刻意的紧凑单行风格冲突且非缺陷,纳入校验会以上万行噪声淹没真问题。
- 集成套件仍是主力回归(535 断言);单测层是其**补集**,专测确定性模块的边界与纯函数,离线可跑。
- 下一步 IR-008:以本绿基线为起点,用红-绿把 `quick_build` 与上游 `relation_discovery` 两份漂移裁决核收敛为一份(DR-035),并落地自引用键(DR-036,`test_quick_build` 里已留反转点)。
