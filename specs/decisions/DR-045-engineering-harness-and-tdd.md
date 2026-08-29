# DR-045 · 工程校验与 TDD 底座

- **状态 / Status**: accepted
- **日期 / Date**: 2026-07-31
- **决策者 / Deciders**: Boss(用户) + Writer(Claude)
- **关联 / Refs**: [[IR-007-tdd-foundation]]、[[DR-046-deterministic-module-unit-tests]];实证=`pyproject.toml`、`.github/workflows/ci.yml`、`.pre-commit-config.yaml`、`tests/meta/test_doc_consistency.py`

## 上下文 / Context

本系统 SDD 规约成熟(34 DR / 6 IR),但工程校验缺失:
无 CI、无 lint 配置、无覆盖率度量、依赖 0 条固定版本。
`pyflakes 零告警`、`535 断言`、`SHACL/HermiT` 全靠**手动**执行;
文档计数已漂移(ARCHITECTURE 称 119 路由/531 断言,实为 121/535)——
连「为本体造漂移检测」的团队,自己的文档漂移都没有校验拦截。
且 535 条 `chk()` 是**实现之后**补写的集成级表征锁,不是**实现之前**的失败验收(非 TDD)。

## 决定 / Decision

1. **单一事实源 `pyproject.toml`**:pytest 发现 + coverage(确定性模块)+ ruff + mypy 全在此声明。
2. **lint 只抓真 bug**:ruff 选 `F`(pyflakes,含重复字典键 F601)+ `B`(bugbear,含闭包捕获 B023);
   **不选** `E7xx/E4xx` —— 本库刻意采用紧凑单行风格(`if x: return y`),风格规则与之冲突且非缺陷,
   纳入校验会以上万行噪声淹没真问题(全规则 1218 条 → F/B 25 条真信号)。
3. **两层测试分工**:集成套件(`test_all.py`,535 断言,需起服务)是主力回归;
   `tests/` 单测层(离线秒级,不起服务)是其补集,专测确定性模块边界与纯函数。
4. **文档-代码计数自检**:`test_doc_consistency.py` 把真实路由/断言数与规范常量对齐,代码一变即红,逼同步文档。
5. **CI 双 job**:unit(单测+覆盖率+lint+mypy,硬挡)+ integration(使用仓库内108表验证库起服务，确定性失败硬挡)。
   外部模型可用性另列条件测试，不得用 `continue-on-error` 或 `|| true` 吞掉确定性失败。
6. **依赖可复现**:`requirements-dev.txt` 固定测试工具链版本;运行时 `requirements.txt` 保留不固定惯例,CI 用 dev 份锁定复现。
7. **UI 测试浏览器可替换**:`test_ui.py`/`test_ui_ops.py` 优先使用
   `DATAMIND_BROWSER_EXECUTABLE`，其次探测本机 Chrome，最后才使用 Playwright 自带 Chromium。
   这样离线或 CDN 不可达环境仍能执行 UI 回归，同时 CI 仍可使用标准 Playwright 浏览器。

## 后果 / Consequences

- (+) 每个既有校验(lint/断言/覆盖率)从「手动」变「合并即挡」;文档漂移被确定性抓住(本轮即订正 119/531→121/535)。
- (+) 确立**已知绿基线**(43 单测绿 / 覆盖率 72.0%),供 IR-008 起算法迭代做红-绿。
- 覆盖率设置 `fail_under=81.0`；当前实测 81.6%，下降到基线以下即阻止合并。
- **规范**:新增 DR/IR 的 `Acceptance` 应尽量先翻成失败断言提交(红)再实现(绿),`test_quick_build` 为首个红-绿范例。
